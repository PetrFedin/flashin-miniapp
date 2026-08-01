from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.services.promos import calculate_discount, validate_promo


def promo(**overrides):
    values = {
        "active": True,
        "expires_at": None,
        "max_uses": 0,
        "used_count": 0,
        "min_amount": 0,
        "discount_type": "percent",
        "discount_value": 10,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_percent_and_fixed_discounts_are_bounded_by_subtotal():
    assert calculate_discount(promo(discount_value=25), 1000) == 250
    assert calculate_discount(promo(discount_type="fixed", discount_value=1500), 1000) == 1000


@pytest.mark.parametrize(
    ("candidate", "detail"),
    [
        (promo(discount_value=-1), "Promo discount cannot be negative"),
        (promo(discount_value=float("nan")), "Invalid promo discount"),
        (promo(min_amount=-1), "Promo minimum cannot be negative"),
        (promo(discount_value=101), "Promo percent cannot exceed 100"),
        (promo(max_uses=-1), "Promo usage counters are invalid"),
        (promo(used_count=-1), "Promo usage counters are invalid"),
        (promo(discount_type="mystery"), "Unsupported promo type"),
    ],
)
def test_invalid_promo_configuration_is_rejected(candidate, detail):
    with pytest.raises(HTTPException) as error:
        validate_promo(candidate, 1000)

    assert error.value.detail == detail


def test_invalid_cart_subtotal_is_rejected():
    with pytest.raises(HTTPException) as error:
        validate_promo(promo(), float("inf"))

    assert error.value.status_code == 409
    assert error.value.detail == "Invalid cart subtotal"
