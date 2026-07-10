"""Real E2E runner template.

Run only against a configured test stack:
RUN_REAL_E2E=1 API_BASE=http://localhost:8000 CUSTOMER_TOKEN=... ADMIN_TOKEN=... pytest backend/tests/e2e
"""
import os
import pytest
import requests

pytestmark = pytest.mark.skipif(os.getenv("RUN_REAL_E2E") != "1", reason="set RUN_REAL_E2E=1")


API = os.getenv("API_BASE", "http://localhost:8000").rstrip("/")
CUSTOMER = os.getenv("CUSTOMER_TOKEN", "")
ADMIN = os.getenv("ADMIN_TOKEN", "")


def headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def test_real_cart_checkout_payment_fulfillment_refund_path():
    assert CUSTOMER, "CUSTOMER_TOKEN required"
    assert ADMIN, "ADMIN_TOKEN required"

    products = requests.get(f"{API}/api/products", timeout=20).json()
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

    checkout = requests.post(
        f"{API}/api/orders/checkout",
        json={"name": "E2E Test", "phone": "+70000000000", "delivery_type": "pickup", "address": "", "comment": "e2e"},
        headers=headers(CUSTOMER),
        timeout=20,
    )
    assert checkout.status_code in (200, 201), checkout.text
    order = checkout.json()

    payment = requests.post(f"{API}/api/payments/orders/{order['id']}", headers=headers(CUSTOMER), timeout=20)
    assert payment.status_code in (200, 201), payment.text

    # Real YooKassa webhook is provider-driven. This test confirms order/payment creation.
    # Full webhook/refund checks are completed by 20-order pilot or provider test callback.
    tasks = requests.get(f"{API}/api/fulfillment/tasks", headers=headers(ADMIN), timeout=20)
    assert tasks.status_code == 200
