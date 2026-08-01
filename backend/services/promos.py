from fastapi import HTTPException

from ..database import utcnow_naive
from ..models import PromoCode


def validate_promo(promo: PromoCode | None, subtotal: float) -> None:
    if not promo or not promo.active:
        raise HTTPException(status_code=404, detail="Promo code not found or inactive")
    if promo.expires_at and promo.expires_at < utcnow_naive():
        raise HTTPException(status_code=409, detail="Promo code expired")
    if promo.max_uses and promo.used_count >= promo.max_uses:
        raise HTTPException(status_code=409, detail="Promo code usage limit reached")
    if subtotal < promo.min_amount:
        raise HTTPException(status_code=409, detail="Cart amount is below promo minimum")


def calculate_discount(promo: PromoCode | None, subtotal: float) -> float:
    if not promo:
        return 0
    validate_promo(promo, subtotal)
    if promo.discount_type == "percent":
        return round(subtotal * promo.discount_value / 100, 2)
    if promo.discount_type == "fixed":
        return min(round(promo.discount_value, 2), subtotal)
    raise HTTPException(status_code=400, detail="Unsupported promo type")
