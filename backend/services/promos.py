from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastapi import HTTPException

from ..database import utcnow_naive
from ..models import PromoCode

_MONEY_STEP = Decimal("0.01")
_HUNDRED = Decimal("100")


def _decimal_number(value: object, field: str) -> Decimal:
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=f"Invalid {field}") from exc
    if not number.is_finite():
        raise HTTPException(status_code=409, detail=f"Invalid {field}")
    return number


def _money(value: object, field: str) -> Decimal:
    return _decimal_number(value, field).quantize(_MONEY_STEP, rounding=ROUND_HALF_UP)


def validate_promo(promo: PromoCode | None, subtotal) -> None:
    normalized_subtotal = _money(subtotal, "cart subtotal")
    if normalized_subtotal < 0:
        raise HTTPException(status_code=409, detail="Cart subtotal cannot be negative")
    if not promo or not promo.active:
        raise HTTPException(status_code=404, detail="Promo code not found or inactive")
    if promo.expires_at and promo.expires_at < utcnow_naive():
        raise HTTPException(status_code=409, detail="Promo code expired")

    min_amount = _money(promo.min_amount, "promo minimum")
    discount_value = _decimal_number(promo.discount_value, "promo discount")
    if min_amount < 0:
        raise HTTPException(status_code=409, detail="Promo minimum cannot be negative")
    if discount_value < 0:
        raise HTTPException(status_code=409, detail="Promo discount cannot be negative")

    if promo.discount_type not in {"percent", "fixed"}:
        raise HTTPException(status_code=400, detail="Unsupported promo type")
    if promo.discount_type == "percent" and discount_value > _HUNDRED:
        raise HTTPException(status_code=409, detail="Promo percent cannot exceed 100")

    if promo.max_uses < 0 or promo.used_count < 0:
        raise HTTPException(status_code=409, detail="Promo usage counters are invalid")
    if promo.max_uses and promo.used_count >= promo.max_uses:
        raise HTTPException(status_code=409, detail="Promo code usage limit reached")
    if normalized_subtotal < min_amount:
        raise HTTPException(status_code=409, detail="Cart amount is below promo minimum")


def calculate_discount(promo: PromoCode | None, subtotal) -> Decimal:
    if not promo:
        return Decimal("0.00")
    normalized_subtotal = _money(subtotal, "cart subtotal")
    validate_promo(promo, normalized_subtotal)
    discount_value = _decimal_number(promo.discount_value, "promo discount")
    if promo.discount_type == "percent":
        return (
            normalized_subtotal * discount_value / _HUNDRED
        ).quantize(_MONEY_STEP, rounding=ROUND_HALF_UP)
    fixed_discount = discount_value.quantize(_MONEY_STEP, rounding=ROUND_HALF_UP)
    return min(fixed_discount, normalized_subtotal)
