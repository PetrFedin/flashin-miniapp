from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from backend.services import promo_definitions
from backend.services.promo_definitions import normalize_promo_definition


def definition(**overrides):
    values = {
        "code": " summer-25 ",
        "discount_type": " percent ",
        "discount_value": 25,
        "min_amount": 1000,
        "max_uses": 10,
        "active": True,
        "expires_at": datetime.now(UTC) + timedelta(days=1),
    }
    values.update(overrides)
    return normalize_promo_definition(**values)


def test_definition_is_normalized_for_persistence():
    result = definition()

    assert result.code == "SUMMER-25"
    assert result.discount_type == "percent"
    assert result.discount_value == 25.0
    assert result.min_amount == 1000.0
    assert result.max_uses == 10
    assert result.active is True
    assert result.expires_at.tzinfo is None


@pytest.mark.parametrize(
    ("overrides", "detail"),
    [
        ({"code": "x"}, "Promo code must contain 3-64 uppercase letters, digits, underscores, or hyphens"),
        ({"code": "bad code"}, "Promo code must contain 3-64 uppercase letters, digits, underscores, or hyphens"),
        ({"discount_type": "tiered"}, "Discount type must be percent or fixed"),
        ({"discount_value": -1}, "Discount value must be non-negative"),
        ({"discount_value": float("nan")}, "Discount value must be non-negative"),
        ({"discount_value": 101}, "Percent discount cannot exceed 100"),
        ({"min_amount": -1}, "Minimum amount must be non-negative"),
        ({"max_uses": 1.5}, "Maximum uses must be a non-negative integer"),
        ({"max_uses": True}, "Maximum uses must be a non-negative integer"),
    ],
)
def test_invalid_definition_is_rejected(overrides, detail):
    with pytest.raises(HTTPException) as error:
        definition(**overrides)

    assert error.value.status_code == 400
    assert error.value.detail == detail


def test_active_expired_definition_is_rejected(monkeypatch):
    now = datetime(2026, 8, 1, 12, 0, 0)
    monkeypatch.setattr(promo_definitions, "utcnow_naive", lambda: now)

    with pytest.raises(HTTPException) as error:
        definition(expires_at=now - timedelta(seconds=1))

    assert error.value.detail == "Active promo code must expire in the future"


def test_inactive_expired_definition_can_be_preserved_for_history(monkeypatch):
    now = datetime(2026, 8, 1, 12, 0, 0)
    monkeypatch.setattr(promo_definitions, "utcnow_naive", lambda: now)

    result = definition(active=False, expires_at=now - timedelta(days=1))

    assert result.active is False
    assert result.expires_at == now - timedelta(days=1)


def test_fixed_discount_is_not_limited_to_100():
    result = definition(discount_type="fixed", discount_value=2500)

    assert result.discount_value == 2500.0
