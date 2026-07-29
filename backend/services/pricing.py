from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import TYPE_CHECKING

from fastapi import HTTPException

if TYPE_CHECKING:
    from ..models import PromoCode

MONEY_STEP = Decimal("0.01")
POINT_STEP = Decimal("0.0001")
PERCENT_STEP = Decimal("0.0001")
HUNDRED = Decimal("100")
MAX_MONEY = Decimal("999999999999999999.99")
MAX_POINTS = Decimal("9999999999999999.9999")
VALID_PROMO_TYPES = frozenset({"percent", "fixed"})


@dataclass(frozen=True)
class PricingBreakdown:
    subtotal: Decimal
    promo_discount: Decimal
    loyalty_points: Decimal
    loyalty_discount: Decimal
    delivery_price: Decimal
    final_amount: Decimal


def decimal_value(
    value: object,
    field: str,
    step: Decimal,
    *,
    maximum: Decimal,
    status_code: int = 409,
) -> Decimal:
    try:
        number = Decimal(str(value)).quantize(step, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(status_code=status_code, detail=f"Invalid {field}")
    if not number.is_finite() or abs(number) > maximum:
        raise HTTPException(status_code=status_code, detail=f"Invalid {field}")
    return number


def money(value: object, field: str, *, status_code: int = 409) -> Decimal:
    return decimal_value(value, field, MONEY_STEP, maximum=MAX_MONEY, status_code=status_code)


def points(value: object, field: str = "loyalty points", *, status_code: int = 409) -> Decimal:
    return decimal_value(value, field, POINT_STEP, maximum=MAX_POINTS, status_code=status_code)


def percent(value: object, field: str, *, status_code: int = 409) -> Decimal:
    return decimal_value(value, field, PERCENT_STEP, maximum=HUNDRED, status_code=status_code)


def normalize_promo_type(value: object, *, status_code: int = 400) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in VALID_PROMO_TYPES:
        raise HTTPException(status_code=status_code, detail="Unsupported promo type")
    return normalized


def validate_promo_definition(
    discount_type: object,
    discount_value: object,
    min_amount: object = 0,
    max_uses: object = 0,
    used_count: object = 0,
    *,
    status_code: int = 400,
) -> tuple[str, Decimal, Decimal, int, int]:
    normalized_type = normalize_promo_type(discount_type, status_code=status_code)
    if normalized_type == "percent":
        value = decimal_value(
            discount_value,
            "promo discount value",
            PERCENT_STEP,
            maximum=HUNDRED,
            status_code=status_code,
        )
    else:
        value = money(discount_value, "promo discount value", status_code=status_code)
    minimum = money(min_amount, "promo minimum amount", status_code=status_code)

    for raw, field in ((max_uses, "promo maximum uses"), (used_count, "promo used count")):
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise HTTPException(status_code=status_code, detail=f"Invalid {field}")
        if raw < 0:
            raise HTTPException(status_code=status_code, detail=f"Invalid {field}")

    if value <= 0:
        raise HTTPException(status_code=status_code, detail="Promo discount value must be positive")
    if minimum < 0:
        raise HTTPException(status_code=status_code, detail="Promo minimum amount cannot be negative")
    if max_uses and used_count > max_uses:
        raise HTTPException(status_code=status_code, detail="Promo usage exceeds its configured limit")
    return normalized_type, value, minimum, max_uses, used_count


def validate_promo(promo: PromoCode | None, subtotal: object, *, now: datetime | None = None) -> Decimal:
    subtotal_value = money(subtotal, "cart subtotal")
    if subtotal_value < 0:
        raise HTTPException(status_code=409, detail="Cart subtotal cannot be negative")
    if not promo or not promo.active:
        raise HTTPException(status_code=404, detail="Promo code not found or inactive")

    _promo_type, _value, minimum, max_uses, used_count = validate_promo_definition(
        promo.discount_type,
        promo.discount_value,
        promo.min_amount,
        promo.max_uses,
        promo.used_count,
        status_code=409,
    )
    current_time = now or datetime.utcnow()
    if promo.expires_at and promo.expires_at < current_time:
        raise HTTPException(status_code=409, detail="Promo code expired")
    if max_uses and used_count >= max_uses:
        raise HTTPException(status_code=409, detail="Promo code usage limit reached")
    if subtotal_value < minimum:
        raise HTTPException(status_code=409, detail="Cart amount is below promo minimum")
    return subtotal_value


def calculate_discount(promo: PromoCode | None, subtotal: object) -> Decimal:
    if not promo:
        return Decimal("0.00")
    subtotal_value = validate_promo(promo, subtotal)
    promo_type, value, _minimum, _max_uses, _used_count = validate_promo_definition(
        promo.discount_type,
        promo.discount_value,
        promo.min_amount,
        promo.max_uses,
        promo.used_count,
        status_code=409,
    )
    if promo_type == "percent":
        discount = (subtotal_value * value / HUNDRED).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
    else:
        discount = value
    return min(max(discount, Decimal("0.00")), subtotal_value)


def calculate_loyalty_discount(loyalty_points: object, point_value: object) -> tuple[Decimal, Decimal]:
    points_value = points(loyalty_points)
    rubles_per_point = money(point_value, "loyalty point value")
    if points_value < 0:
        raise HTTPException(status_code=409, detail="Loyalty points cannot be negative")
    if rubles_per_point <= 0:
        raise HTTPException(status_code=500, detail="Loyalty point value is misconfigured")
    discount = (points_value * rubles_per_point).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
    return points_value, discount


def maximum_loyalty_discount(
    subtotal: object,
    promo_discount: object,
    max_redeem_percent: object,
) -> Decimal:
    subtotal_value = money(subtotal, "cart subtotal")
    promo_value = money(promo_discount, "promo discount")
    limit = percent(max_redeem_percent, "loyalty redemption limit", status_code=500)
    if limit < 0 or limit > HUNDRED:
        raise HTTPException(status_code=500, detail="Loyalty redemption limit is misconfigured")
    if promo_value < 0 or promo_value > subtotal_value:
        raise HTTPException(status_code=409, detail="Promo discount is invalid")
    percentage_limit = (subtotal_value * limit / HUNDRED).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
    payable_after_promo = subtotal_value - promo_value
    return min(percentage_limit, payable_after_promo)


def calculate_pricing(
    *,
    subtotal: object,
    promo: PromoCode | None,
    loyalty_points: object,
    point_value: object,
    max_redeem_percent: object,
    delivery_price: object = 0,
    require_positive_total: bool = False,
) -> PricingBreakdown:
    subtotal_value = money(subtotal, "cart subtotal")
    delivery_value = money(delivery_price, "delivery price")
    if subtotal_value < 0:
        raise HTTPException(status_code=409, detail="Cart subtotal cannot be negative")
    if delivery_value < 0:
        raise HTTPException(status_code=409, detail="Delivery price cannot be negative")

    promo_discount = calculate_discount(promo, subtotal_value)
    points_value, loyalty_discount = calculate_loyalty_discount(loyalty_points, point_value)
    allowed_loyalty = maximum_loyalty_discount(
        subtotal_value,
        promo_discount,
        max_redeem_percent,
    )
    if loyalty_discount > allowed_loyalty:
        raise HTTPException(status_code=409, detail="Loyalty redemption exceeds the allowed limit")

    final_amount = (subtotal_value - promo_discount - loyalty_discount + delivery_value).quantize(
        MONEY_STEP,
        rounding=ROUND_HALF_UP,
    )
    if final_amount < 0 or (require_positive_total and final_amount <= 0):
        raise HTTPException(status_code=409, detail="Order total must be positive")
    return PricingBreakdown(
        subtotal=subtotal_value,
        promo_discount=promo_discount,
        loyalty_points=points_value,
        loyalty_discount=loyalty_discount,
        delivery_price=delivery_value,
        final_amount=final_amount,
    )
