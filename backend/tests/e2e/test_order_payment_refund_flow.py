"""E2E scaffold for real checkout/payment/refund flow.

These tests are intentionally guarded by RUN_REAL_E2E=1 because they require
live services and configured credentials.
"""
import os
import pytest

pytestmark = pytest.mark.skipif(os.getenv("RUN_REAL_E2E") != "1", reason="set RUN_REAL_E2E=1 to run real e2e tests")


def test_cart_checkout_payment_fulfillment_refund_flow():
    # Implement against a running stack with real Telegram auth and YooKassa test mode.
    # Steps:
    # 1. auth
    # 2. add cart
    # 3. checkout
    # 4. create payment
    # 5. process webhook
    # 6. assert fulfillment
    # 7. create refund
    # 8. assert loyalty return
    assert True
