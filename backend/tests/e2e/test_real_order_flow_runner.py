"""Guarded real-provider E2E runner.

Run only against an explicitly configured pilot/test stack with an allowlisted
customer and an explicitly selected controlled product variant. This runner
creates a real order and a real YooKassa payment attempt; provider-driven
settlement/refund evidence is captured by the controlled pilot lifecycle rather
than fabricated by this test.

RUN_REAL_E2E=1 API_BASE=https://api.example.test CUSTOMER_TOKEN=... ADMIN_TOKEN=... \
  E2E_VARIANT_ID=456 pytest -q backend/tests/e2e/test_real_order_flow_runner.py
"""

import os
import uuid

import pytest
import requests

pytestmark = pytest.mark.skipif(os.getenv("RUN_REAL_E2E") != "1", reason="set RUN_REAL_E2E=1")

API = os.getenv("API_BASE", "http://localhost:8000").rstrip("/")
CUSTOMER = os.getenv("CUSTOMER_TOKEN", "")
ADMIN = os.getenv("ADMIN_TOKEN", "")


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

    cart = requests.post(
        f"{API}/api/cart/items",
        json={"product_id": product["id"], "variant_id": variant["id"], "quantity": 1},
        headers=headers(CUSTOMER),
        timeout=20,
    )
    assert cart.status_code in (200, 201), cart.text

    idempotency_key = f"real-e2e:{uuid.uuid4().hex}"
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

    # The current payment API takes order_id in the body. Older runners used the
    # removed /api/payments/orders/{id} route and therefore did not exercise the
    # production contract.
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

    # This is deliberately read-only after payment creation. The live YooKassa
    # callback, fulfillment, refund, stock return and Telegram delivery are
    # provider/operator-driven and must be recorded through pilot lifecycle evidence.
    tasks = requests.get(f"{API}/api/fulfillment/tasks", headers=headers(ADMIN), timeout=20)
    assert tasks.status_code == 200, tasks.text
