from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.cart_schemas import CartOut


ROOT = Path(__file__).resolve().parents[2]
CART_API = ROOT / "backend" / "api" / "cart.py"


def _cart(**overrides) -> CartOut:
    payload = {
        "id": 1,
        "items": [],
        "total_amount": 2000,
        "discount_amount": 300,
        "promo_discount_amount": 200,
        "loyalty_points_reserved": 100,
        "loyalty_discount_amount": 100,
        "final_amount": 1700,
        "promo_code": "SAVE10",
    }
    payload.update(overrides)
    return CartOut(**payload)


def test_cart_contract_exposes_complete_pricing_breakdown():
    cart = _cart()

    assert cart.total_amount == 2000
    assert cart.discount_amount == 300
    assert cart.promo_discount_amount == 200
    assert cart.loyalty_points_reserved == 100
    assert cart.loyalty_discount_amount == 100
    assert cart.final_amount == 1700


def test_cart_contract_rejects_mismatched_discount_components():
    with pytest.raises(ValidationError, match="Cart discount does not match"):
        _cart(discount_amount=250)


def test_cart_contract_rejects_mismatched_final_amount():
    with pytest.raises(ValidationError, match="Cart final amount does not match"):
        _cart(final_amount=1800)


def test_cart_contract_rejects_non_finite_values():
    with pytest.raises(ValidationError):
        _cart(total_amount=float("inf"))


def test_cart_routes_use_the_strict_pricing_contract_and_component_values():
    source = CART_API.read_text(encoding="utf-8")

    assert "from ..cart_schemas import CartOut" in source
    assert "from ..schemas import CartAddIn, CartItemOut, LoyaltyRedeemIn" in source
    assert "total_discount = (promo_discount + loyalty_discount)" in source
    assert "discount_amount=float(total_discount)" in source
    assert "promo_discount_amount=float(promo_discount)" in source
    assert "loyalty_points_reserved=loyalty_points" in source
    assert "loyalty_discount_amount=float(loyalty_discount)" in source
    assert source.count("response_model=CartOut") == 8
