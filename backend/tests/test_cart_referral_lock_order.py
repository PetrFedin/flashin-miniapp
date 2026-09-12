import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api import cart as cart_api


def test_referral_mutation_locks_customer_before_cart(monkeypatch):
    calls: list[tuple[str, int]] = []
    locked_cart = SimpleNamespace(id=11)

    def attach(db, code, customer_id):
        calls.append(("customer", customer_id))
        return True

    def lock_cart(db, cart_id):
        calls.append(("cart", cart_id))
        return locked_cart

    monkeypatch.setattr(cart_api, "attach_referral_to_customer", attach)
    monkeypatch.setattr(cart_api, "_lock_cart", lock_cart)

    result = cart_api._attach_referral_then_lock_cart(
        object(),
        "FLTEST",
        customer_id=7,
        cart_id=11,
    )

    assert result is locked_cart
    assert calls == [("customer", 7), ("cart", 11)]


def test_referral_mutation_does_not_lock_cart_for_invalid_code(monkeypatch):
    calls: list[str] = []

    def attach(db, code, customer_id):
        calls.append("customer")
        return False

    def lock_cart(db, cart_id):
        calls.append("cart")
        raise AssertionError("cart must not be locked after referral rejection")

    monkeypatch.setattr(cart_api, "attach_referral_to_customer", attach)
    monkeypatch.setattr(cart_api, "_lock_cart", lock_cart)

    with pytest.raises(HTTPException) as exc_info:
        cart_api._attach_referral_then_lock_cart(
            object(),
            "MISSING",
            customer_id=7,
            cart_id=11,
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Referral code not found or unavailable"
    assert calls == ["customer"]


@pytest.mark.skipif(
    not os.getenv("DATABASE_URL", "").startswith("postgresql"),
    reason="requires PostgreSQL row-lock semantics",
)
def test_postgres_referral_checkout_lock_order_smoke():
    from scripts.cart_referral_checkout_lock_order_smoke import main

    assert main() == 0
