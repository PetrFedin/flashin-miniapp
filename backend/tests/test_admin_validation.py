import pytest
from fastapi import HTTPException

from backend.api.admin import _clean, _positive_price, _stock_quantity


def test_clean_trims_and_limits_values():
    assert _clean("  FLASHIN  ", "Brand", 20) == "FLASHIN"

    with pytest.raises(HTTPException) as empty_error:
        _clean("   ", "Brand", 20)
    assert empty_error.value.status_code == 400

    with pytest.raises(HTTPException) as length_error:
        _clean("x" * 21, "Brand", 20)
    assert length_error.value.status_code == 400


def test_positive_price_accepts_finite_positive_value():
    assert _positive_price("1000.125") == 1000.12


@pytest.mark.parametrize("value", [0, -1, "invalid", float("nan"), float("inf")])
def test_positive_price_rejects_invalid_value(value):
    with pytest.raises(HTTPException) as exc:
        _positive_price(value)

    assert exc.value.status_code == 400


@pytest.mark.parametrize(("value", "expected"), [(0, 0), (5, 5), (5.0, 5), ("5", 5)])
def test_stock_quantity_accepts_non_negative_integer(value, expected):
    assert _stock_quantity(value) == expected


@pytest.mark.parametrize("value", [-1, True, 1.5, "1.5", "5.0", "invalid", None])
def test_stock_quantity_rejects_invalid_value(value):
    with pytest.raises(HTTPException) as exc:
        _stock_quantity(value)

    assert exc.value.status_code == 400
