from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api.orders import _clean_required, _money, _validate_cart_for_checkout


def _cart_item(
    *,
    item_id=1,
    product_id=10,
    variant_id=20,
    quantity=1,
    product_active=True,
    product_price=1000,
    variant_product_id=10,
):
    product = SimpleNamespace(
        id=product_id,
        active=product_active,
        price=product_price,
        title="Test product",
    )
    variant = SimpleNamespace(id=variant_id, product_id=variant_product_id)
    return SimpleNamespace(
        id=item_id,
        product_id=product_id,
        variant_id=variant_id,
        quantity=quantity,
        product=product,
        variant=variant,
    )


def test_money_rounds_to_two_decimals():
    assert _money("10.005", "amount") == Decimal("10.01")


@pytest.mark.parametrize("value", [None, "abc", float("nan"), float("inf")])
def test_money_rejects_invalid_values(value):
    with pytest.raises(HTTPException) as exc:
        _money(value, "amount")

    assert exc.value.status_code == 409


def test_clean_required_trims_value():
    assert _clean_required("  Petr  ", "Name", 20) == "Petr"


@pytest.mark.parametrize("value", ["", "   ", None])
def test_clean_required_rejects_empty_value(value):
    with pytest.raises(HTTPException) as exc:
        _clean_required(value, "Name", 20)

    assert exc.value.status_code == 400


def test_cart_validation_accepts_consistent_item():
    _validate_cart_for_checkout(SimpleNamespace(items=[_cart_item()]))


def test_cart_validation_rejects_duplicate_variant():
    cart = SimpleNamespace(
        items=[
            _cart_item(item_id=1, variant_id=20),
            _cart_item(item_id=2, variant_id=20),
        ]
    )

    with pytest.raises(HTTPException) as exc:
        _validate_cart_for_checkout(cart)

    assert exc.value.status_code == 409


@pytest.mark.parametrize(
    "item",
    [
        _cart_item(quantity=0),
        _cart_item(product_active=False),
        _cart_item(product_price=-1),
        _cart_item(variant_product_id=99),
    ],
)
def test_cart_validation_rejects_inconsistent_item(item):
    with pytest.raises(HTTPException) as exc:
        _validate_cart_for_checkout(SimpleNamespace(items=[item]))

    assert exc.value.status_code == 409
