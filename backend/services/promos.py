import math

from fastapi import HTTPException

from ..database import utcnow_naive
from ..models import PromoCode


def _finite_number(value: object, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=409, detail=f"Invalid {field}")
    if not math.isfinite(number):
        raise HTTPException(status_code=409, detail=f"Invalid {field}")
    return number


def validate_promo(promo: PromoCode | None, subtotal: float) -> None:
    normalized_subtotal = _finite_number(subtotal, "cart subtotal")
    if normalized_subtotal < 0:
        raise HTTPException(status_code=409, detail="Cart subtotal cannot be negative")
    if not promo or not promo.active:
        raise HTTPException(status_code=404, detail="Promo code not found or inactive")
    if promo.expires_at and promo.expires_at < utcnow_naive():
        raise HTTPException(status_code=409, detail="Promo code expired")

    min_amount = _finite_number(promo.min_amount, "promo minimum")
    discount_value = _finite_number(promo.discount_value, "promo discount")
    if min_amount < 0:
        raise HTTPException(status_code=409, detail="Promo minimum cannot be negative")
    if discount_value < 0:
        raise HTTPException(status_code=409, detail="Promo discount cannot be negative")

    if promo.discount_type not in {"percent", "fixed"}:
        raise HTTPException(status_code=400, detail="Unsupported promo type")
    if promo.discount_type == "percent" and discount_value > 100:
        raise HTTPException(status_code=409, detail="Promo percent cannot exceed 100")

    if promo.max_uses < 0 or promo.used_count < 0:
        raise HTTPException(status_code=409, detail="Promo usage counters are invalid")
    if promo.max_uses and promo.used_count >= promo.max_uses:
        raise HTTPException(status_code=409, detail="Promo code usage limit reached")
    if normalized_subtotal < min_amount:
        raise HTTPException(status_code=409, detail="Cart amount is below promo minimum")


def calculate_discount(promo: PromoCode | None, subtotal: float) -> float:
    if not promo:
        return 0
    validate_promo(promo, subtotal)
    discount_value = float(promo.discount_value)
    if promo.discount_type == "percent":
        return round(float(subtotal) * discount_value / 100, 2)
    return min(round(discount_value, 2), float(subtotal))
