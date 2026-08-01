import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import utcnow_naive
from ..models import Cart, CrmProfile, LoyaltyRedemptionHold, PromoCode
from .promos import calculate_discount


_MONEY_STEP = Decimal("0.01")


@dataclass(frozen=True)
class CartAdjustmentResult:
    subtotal: Decimal
    promo_discount: Decimal
    loyalty_points: int
    loyalty_discount: Decimal
    promo_code: str | None

    @property
    def final_amount(self) -> Decimal:
        return max(
            self.subtotal - self.promo_discount - self.loyalty_discount,
            Decimal("0.00"),
        )


def _money(value: object, field: str) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(_MONEY_STEP, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(status_code=409, detail=f"Invalid {field}")
    if not amount.is_finite():
        raise HTTPException(status_code=409, detail=f"Invalid {field}")
    return amount


def _finite_non_negative(value: object, field: str) -> Decimal:
    amount = _money(value, field)
    if amount < 0:
        raise HTTPException(status_code=409, detail=f"{field.capitalize()} cannot be negative")
    return amount


def _cart_subtotal(cart: Cart) -> Decimal:
    subtotal = Decimal("0.00")
    for item in cart.items:
        if not item.product:
            raise HTTPException(status_code=409, detail=f"Cart item {item.id} has no product")
        if isinstance(item.quantity, bool) or not isinstance(item.quantity, int) or item.quantity <= 0:
            raise HTTPException(status_code=409, detail=f"Cart item {item.id} has invalid quantity")
        price = _finite_non_negative(item.product.price, "product price")
        subtotal += price * item.quantity
    return subtotal.quantize(_MONEY_STEP, rounding=ROUND_HALF_UP)


def _release_hold(hold: LoyaltyRedemptionHold | None) -> None:
    if not hold:
        return
    hold.status = "released"
    hold.released_at = utcnow_naive()


def _reserved_holds(db: Session, cart: Cart) -> tuple[list[LoyaltyRedemptionHold], LoyaltyRedemptionHold | None]:
    holds = (
        db.query(LoyaltyRedemptionHold)
        .filter(
            LoyaltyRedemptionHold.customer_id == cart.customer_id,
            LoyaltyRedemptionHold.status == "reserved",
        )
        .with_for_update()
        .all()
    )
    current_holds = [hold for hold in holds if hold.cart_id == cart.id]
    current_hold = current_holds[0] if current_holds else None
    for duplicate_hold in current_holds[1:]:
        _release_hold(duplicate_hold)
    return holds, current_hold


def _reconcile_promo(db: Session, cart: Cart, subtotal: Decimal) -> tuple[Decimal, str | None]:
    if not cart.promo_code_id:
        return Decimal("0.00"), None

    promo = (
        db.query(PromoCode)
        .filter(PromoCode.id == cart.promo_code_id)
        .with_for_update()
        .first()
    )
    try:
        discount = _money(calculate_discount(promo, float(subtotal)), "promo discount")
        if discount < 0 or discount > subtotal:
            raise HTTPException(status_code=409, detail="Promo discount is invalid")
    except HTTPException:
        cart.promo_code_id = None
        if hasattr(cart, "promo_code"):
            cart.promo_code = None
        return Decimal("0.00"), None

    return discount, promo.code


def _whole_requested_points(value: object) -> int:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(number) or number <= 0 or not number.is_integer():
        return 0
    return int(number)


def _strict_requested_points(value: object) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Invalid loyalty points")
    if not math.isfinite(number) or number < 0:
        raise HTTPException(status_code=422, detail="Loyalty points must be non-negative")
    if not number.is_integer():
        raise HTTPException(status_code=422, detail="Loyalty points must be whole numbers")
    return int(number)


def _loyalty_settings() -> tuple[Decimal, Decimal]:
    settings = get_settings()
    point_value = _finite_non_negative(settings.loyalty_point_value_rub, "loyalty point value")
    max_percent = _finite_non_negative(settings.loyalty_max_redeem_percent, "loyalty limit")
    if point_value <= 0:
        raise HTTPException(status_code=500, detail="Loyalty point value must be positive")
    if max_percent > 100:
        raise HTTPException(status_code=500, detail="Loyalty limit cannot exceed 100 percent")
    return point_value, max_percent


def _allowed_loyalty_points(
    *,
    profile_points: Decimal,
    other_reserved: Decimal,
    subtotal: Decimal,
    promo_discount: Decimal,
    point_value: Decimal,
    max_percent: Decimal,
) -> int:
    available_points = max(profile_points - other_reserved, Decimal("0.00"))
    maximum_discount = (subtotal * max_percent / Decimal("100")).quantize(
        _MONEY_STEP,
        rounding=ROUND_HALF_UP,
    )
    payable_before_loyalty = max(subtotal - promo_discount, Decimal("0.00"))
    allowed_discount = min(maximum_discount, payable_before_loyalty)
    points_by_money = int((allowed_discount / point_value).to_integral_value(rounding=ROUND_DOWN))
    points_by_balance = int(available_points.to_integral_value(rounding=ROUND_DOWN))
    return max(min(points_by_money, points_by_balance), 0)


def _reconcile_loyalty(
    db: Session,
    cart: Cart,
    subtotal: Decimal,
    promo_discount: Decimal,
) -> tuple[int, Decimal]:
    holds, current_hold = _reserved_holds(db, cart)
    requested_points = _whole_requested_points(cart.loyalty_points_to_redeem)
    if requested_points <= 0:
        cart.loyalty_points_to_redeem = 0
        _release_hold(current_hold)
        return 0, Decimal("0.00")

    profile = (
        db.query(CrmProfile)
        .filter(CrmProfile.customer_id == cart.customer_id)
        .with_for_update()
        .first()
    )
    if not profile:
        cart.loyalty_points_to_redeem = 0
        _release_hold(current_hold)
        return 0, Decimal("0.00")

    profile_points = _finite_non_negative(profile.loyalty_points, "loyalty balance")
    other_reserved = sum(
        (
            _finite_non_negative(hold.points, "reserved loyalty points")
            for hold in holds
            if hold.cart_id != cart.id
        ),
        Decimal("0.00"),
    )
    point_value, max_percent = _loyalty_settings()
    allowed_points = _allowed_loyalty_points(
        profile_points=profile_points,
        other_reserved=other_reserved,
        subtotal=subtotal,
        promo_discount=promo_discount,
        point_value=point_value,
        max_percent=max_percent,
    )
    effective_points = min(requested_points, allowed_points)

    if effective_points <= 0:
        cart.loyalty_points_to_redeem = 0
        _release_hold(current_hold)
        return 0, Decimal("0.00")

    cart.loyalty_points_to_redeem = effective_points
    if current_hold:
        current_hold.points = effective_points
        current_hold.released_at = None
    else:
        db.add(
            LoyaltyRedemptionHold(
                customer_id=cart.customer_id,
                cart_id=cart.id,
                points=effective_points,
                status="reserved",
            )
        )

    loyalty_discount = (Decimal(effective_points) * point_value).quantize(
        _MONEY_STEP,
        rounding=ROUND_HALF_UP,
    )
    return effective_points, loyalty_discount


def reconcile_cart_adjustments(db: Session, cart: Cart) -> CartAdjustmentResult:
    subtotal = _cart_subtotal(cart)
    promo_discount, promo_code = _reconcile_promo(db, cart, subtotal)
    loyalty_points, loyalty_discount = _reconcile_loyalty(
        db,
        cart,
        subtotal,
        promo_discount,
    )
    return CartAdjustmentResult(
        subtotal=subtotal,
        promo_discount=promo_discount,
        loyalty_points=loyalty_points,
        loyalty_discount=loyalty_discount,
        promo_code=promo_code,
    )


def apply_loyalty_request(db: Session, cart: Cart, points: object) -> CartAdjustmentResult:
    requested_points = _strict_requested_points(points)
    baseline = reconcile_cart_adjustments(db, cart)
    holds, current_hold = _reserved_holds(db, cart)

    if requested_points == 0:
        cart.loyalty_points_to_redeem = 0
        _release_hold(current_hold)
        return CartAdjustmentResult(
            subtotal=baseline.subtotal,
            promo_discount=baseline.promo_discount,
            loyalty_points=0,
            loyalty_discount=Decimal("0.00"),
            promo_code=baseline.promo_code,
        )

    profile = (
        db.query(CrmProfile)
        .filter(CrmProfile.customer_id == cart.customer_id)
        .with_for_update()
        .first()
    )
    if not profile:
        raise HTTPException(status_code=409, detail="Loyalty profile not found")

    profile_points = _finite_non_negative(profile.loyalty_points, "loyalty balance")
    other_reserved = sum(
        (
            _finite_non_negative(hold.points, "reserved loyalty points")
            for hold in holds
            if hold.cart_id != cart.id
        ),
        Decimal("0.00"),
    )
    point_value, max_percent = _loyalty_settings()
    allowed_points = _allowed_loyalty_points(
        profile_points=profile_points,
        other_reserved=other_reserved,
        subtotal=baseline.subtotal,
        promo_discount=baseline.promo_discount,
        point_value=point_value,
        max_percent=max_percent,
    )
    if requested_points > allowed_points:
        raise HTTPException(
            status_code=409,
            detail=f"No more than {allowed_points} loyalty points can be redeemed for this cart",
        )

    cart.loyalty_points_to_redeem = requested_points
    if current_hold:
        current_hold.points = requested_points
        current_hold.status = "reserved"
        current_hold.released_at = None
    else:
        db.add(
            LoyaltyRedemptionHold(
                customer_id=cart.customer_id,
                cart_id=cart.id,
                points=requested_points,
                status="reserved",
            )
        )

    loyalty_discount = (Decimal(requested_points) * point_value).quantize(
        _MONEY_STEP,
        rounding=ROUND_HALF_UP,
    )
    return CartAdjustmentResult(
        subtotal=baseline.subtotal,
        promo_discount=baseline.promo_discount,
        loyalty_points=requested_points,
        loyalty_discount=loyalty_discount,
        promo_code=baseline.promo_code,
    )
