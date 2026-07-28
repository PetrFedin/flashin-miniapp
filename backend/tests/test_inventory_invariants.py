from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.services.inventory import (
    _validate_positive_quantity,
    _validate_stock_quantity,
    _validate_variant_state,
)


def test_positive_quantity_accepts_positive_integer():
    _validate_positive_quantity(1)
    _validate_positive_quantity(10)


@pytest.mark.parametrize("quantity", [0, -1, 1.5, "1", True, None])
def test_positive_quantity_rejects_invalid_values(quantity):
    with pytest.raises(HTTPException) as exc:
        _validate_positive_quantity(quantity)

    assert exc.value.status_code == 400


def test_stock_quantity_allows_zero():
    _validate_stock_quantity(0)
    _validate_stock_quantity(5)


@pytest.mark.parametrize("quantity", [-1, 1.5, "1", True, None])
def test_stock_quantity_rejects_invalid_values(quantity):
    with pytest.raises(HTTPException) as exc:
        _validate_stock_quantity(quantity)

    assert exc.value.status_code == 400


def test_variant_state_accepts_consistent_inventory():
    _validate_variant_state(SimpleNamespace(stock_qty=10, reserved_qty=0))
    _validate_variant_state(SimpleNamespace(stock_qty=10, reserved_qty=10))


@pytest.mark.parametrize(
    ("stock_qty", "reserved_qty"),
    [
        (-1, 0),
        (0, -1),
        (5, 6),
    ],
)
def test_variant_state_rejects_corruption(stock_qty, reserved_qty):
    with pytest.raises(HTTPException) as exc:
        _validate_variant_state(SimpleNamespace(stock_qty=stock_qty, reserved_qty=reserved_qty))

    assert exc.value.status_code == 409
