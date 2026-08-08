"""Guarded real-provider E2E runner.

Run only against an explicitly configured pilot/test stack with an allowlisted
customer. This runner creates a real order and a real YooKassa payment attempt;
provider-driven settlement/refund evidence is captured by the controlled pilot
lifecycle rather than fabricated by this test.

RUN_REAL_E2E=1 API_BASE=https://api.example.test CUSTOMER_TOKEN=... ADMIN_TOKEN=... \
  pytest -q backend/tests/e2e/test_real_order_flow_runner.py
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


def test_real_cart_checkout_and_yookassa_payment_creation():
    assert CUSTOMER, "CUSTOMER_TOKEN required"
    assert ADMIN, "ADMIN_TOKEN required"

    products_response = requests.get(f"{API}/api/products", timeout=20)
    assert products_response.status_code == 200, products_response.text
    products = products_response.json()
    assert products, "Need at least one active product"
    product = products[0]
    variant = next(v for v in product["variants"] if v.get("available_qty", 0) > 0)

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
