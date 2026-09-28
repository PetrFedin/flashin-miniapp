"""Guarded real-provider E2E runner.

Run only against an explicitly configured pilot/test stack with an allowlisted
customer and an explicitly selected controlled product variant. This runner
creates a real order and a real YooKassa payment attempt, then writes a sanitized
context artifact used by the terminal lifecycle verifier. It atomically claims
the private context before the first cart mutation, preventing concurrent real
payment runs, and persists a provisional context before checkout so a process
crash cannot silently permit a second payment attempt. Provider-driven
settlement/refund evidence is never fabricated by this test.

RUN_REAL_E2E=1 API_BASE=https://api.example.test CUSTOMER_TOKEN=... ADMIN_TOKEN=... \
  E2E_VARIANT_ID=456 pytest -q backend/tests/e2e/test_real_order_flow_runner.py
"""

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import requests

pytestmark = pytest.mark.skipif(os.getenv("RUN_REAL_E2E") != "1", reason="set RUN_REAL_E2E=1")

API = os.getenv("API_BASE", "http://localhost:8000").rstrip("/")
CUSTOMER = os.getenv("CUSTOMER_TOKEN", "")
ADMIN = os.getenv("ADMIN_TOKEN", "")
CONTEXT_FILE = Path(
    os.getenv(
        "E2E_CONTEXT_FILE",
        "docs/pilot/evidence/real_order_e2e_context.json",
    )
)


def headers(token: str, **extra: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        **extra,
    }


def _required_int(name: str) -> int:
    raw = str(os.getenv(name, "")).strip()
    assert raw.isdigit() and int(raw) > 0, f"{name} must be a positive integer"
    return int(raw)


def _serialize_context(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _fsync_context_directory() -> None:
    directory_fd = os.open(str(CONTEXT_FILE.parent), os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _claim_context(payload: dict[str, object]) -> None:
    """Atomically claim the real-E2E slot before the first external mutation."""

    CONTEXT_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(
            str(CONTEXT_FILE),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        raise AssertionError(
            f"Real E2E context already exists at {CONTEXT_FILE}. "
            "Finish/archive or explicitly investigate the previous controlled lifecycle before "
            "creating another real payment. Provisional phases are intentionally fail-closed."
        ) from None
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(_serialize_context(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(CONTEXT_FILE, 0o600)
        _fsync_context_directory()
    except Exception:
        # Keep a successfully created marker fail-closed. If writing the marker
        # itself did not complete, the next operator must inspect/remove it.
        raise


def _write_context(payload: dict[str, object]) -> None:
    """Durably replace the lifecycle marker before/after external side effects."""

    CONTEXT_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONTEXT_FILE.with_name(f".{CONTEXT_FILE.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(_serialize_context(payload))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        temporary.replace(CONTEXT_FILE)
        _fsync_context_directory()
    finally:
        temporary.unlink(missing_ok=True)


def test_real_cart_checkout_and_yookassa_payment_creation():
    assert CUSTOMER, "CUSTOMER_TOKEN required"
    assert ADMIN, "ADMIN_TOKEN required"
    variant_id = _required_int("E2E_VARIANT_ID")

    products_response = requests.get(f"{API}/api/products", timeout=20)
    assert products_response.status_code == 200, products_response.text
    products = products_response.json()
    controlled_matches = [
        (product, variant)
        for product in products
        for variant in product.get("variants", [])
        if variant.get("id") == variant_id
    ]
    assert len(controlled_matches) == 1, (
        f"Expected exactly one controlled variant {variant_id}, "
        f"found {len(controlled_matches)}"
    )
    product, variant = controlled_matches[0]
    assert variant.get("available_qty", 0) > 0, (
        f"Controlled variant {variant_id} must have available stock"
    )

    inventory_response = requests.get(
        f"{API}/api/admin/products",
        headers=headers(ADMIN),
        timeout=20,
    )
    assert inventory_response.status_code == 200, inventory_response.text
    inventory_matches = [
        inventory_variant
        for inventory_product in inventory_response.json()
        for inventory_variant in inventory_product.get("variants", [])
        if inventory_variant.get("id") == variant_id
    ]
    assert len(inventory_matches) == 1, (
        f"Expected exactly one admin inventory variant {variant_id}, "
        f"found {len(inventory_matches)}"
    )
    inventory_variant = inventory_matches[0]
    baseline_stock_qty = int(inventory_variant.get("stock_qty", -1))
    baseline_reserved_qty = int(inventory_variant.get("reserved_qty", -1))
    assert baseline_stock_qty > 0, inventory_variant
    assert baseline_reserved_qty == 0, (
        "Controlled pilot variant must have zero existing reservations before real E2E"
    )

    baseline_cart_response = requests.get(
        f"{API}/api/cart",
        headers=headers(CUSTOMER),
        timeout=20,
    )
    assert baseline_cart_response.status_code == 200, baseline_cart_response.text
    baseline_cart = baseline_cart_response.json()
    assert baseline_cart.get("items") == [], (
        "Controlled pilot customer cart must be empty before the real payment E2E"
    )
    assert not baseline_cart.get("promo_code"), (
        "Controlled pilot customer cart must not have a promo before the real payment E2E"
    )
    assert int(baseline_cart.get("loyalty_points_reserved") or 0) == 0, (
        "Controlled pilot customer cart must not reserve loyalty points before the real payment E2E"
    )

    context_created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    idempotency_key = f"real-e2e:{uuid.uuid4().hex}"
    context_base = {
        "schema_version": 1,
        "kind": "flashin_real_order_e2e_context",
        "created_at": context_created_at,
        "api_base": API,
        "product_id": int(product["id"]),
        "variant_id": variant_id,
        "quantity": 1,
        "baseline_stock_qty": baseline_stock_qty,
        "baseline_reserved_qty": baseline_reserved_qty,
        "provider": "yookassa",
        "checkout_idempotency_key": idempotency_key,
    }

    # This O_EXCL claim is the concurrency boundary. Only one operator/process
    # can proceed from read-only preconditions into cart/order/payment mutations.
    _claim_context(
        {
            **context_base,
            "phase": "preflight_intent",
        }
    )

    cart = requests.post(
        f"{API}/api/cart/items",
        json={"product_id": product["id"], "variant_id": variant["id"], "quantity": 1},
        headers=headers(CUSTOMER),
        timeout=20,
    )
    assert cart.status_code in (200, 201), cart.text
    controlled_cart = cart.json()
    assert len(controlled_cart.get("items", [])) == 1, controlled_cart
    assert controlled_cart["items"][0].get("variant_id") == variant_id, controlled_cart
    assert controlled_cart["items"][0].get("quantity") == 1, controlled_cart

    # Persist an intent before checkout. If the process or network dies after the
    # server accepts checkout, this marker survives and blocks a fresh real-payment
    # run until an operator explicitly investigates the prior attempt.
    _write_context(
        {
            **context_base,
            "phase": "checkout_intent",
        }
    )

    checkout = requests.post(
        f"{API}/api/orders/checkout",
        json={
            "name": "E2E Test",
            "phone": "+70000000000",
            "delivery_type": "pickup",
            "address": "",
            "comment": "guarded real-provider e2e",
        },
        headers=headers(CUSTOMER, **{"Idempotency-Key": idempotency_key}),
        timeout=20,
    )
    assert checkout.status_code in (200, 201), checkout.text
    order = checkout.json()
    assert order["id"] > 0
    assert order["payment_status"] == "pending"
    assert len(order.get("items", [])) == 1, order
    assert order["items"][0].get("variant_id") == variant_id, order
    assert order["items"][0].get("quantity") == 1, order
    assert abs(float(order["total_amount"]) - float(product["price"])) < 0.01, order

    order_context = {
        **context_base,
        "phase": "order_created",
        "subject_id": f"order:{int(order['id'])}",
        "order_id": int(order["id"]),
    }
    _write_context(order_context)

    payment = requests.post(
        f"{API}/api/payments",
        json={"order_id": order["id"]},
        headers=headers(CUSTOMER),
        timeout=30,
    )
    assert payment.status_code in (200, 201), payment.text
    payment_data = payment.json()
    assert payment_data["order_id"] == order["id"]
    assert payment_data["provider"] == "yookassa"
    assert payment_data["provider_payment_id"]
    assert payment_data["status"] in {"pending", "waiting_for_capture", "succeeded"}
    if payment_data["status"] != "succeeded":
        assert payment_data["confirmation_url"], "Pending YooKassa payment must have confirmation URL"

    _write_context(
        {
            **order_context,
            "phase": "payment_created",
            "provider_payment_id": str(payment_data["provider_payment_id"]),
        }
    )

    # Deliberately read-only after payment creation. The live YooKassa callback,
    # fulfillment, refund, stock return and Telegram delivery are provider/operator
    # driven and must be recorded through pilot lifecycle evidence.
    tasks = requests.get(f"{API}/api/fulfillment/tasks", headers=headers(ADMIN), timeout=20)
    assert tasks.status_code == 200, tasks.text