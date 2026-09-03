from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import utcnow_naive
from ..models import Cart, CrmProfile, LoyaltyRedemptionHold, PromoCode
from .pricing import load_product_price_quotes
from .promos import calculate_discount


_MONEY_STEP = Decimal("0.01")
_POINTS_STEP = Decimal("0.0001")


@dataclass(frozen=True)
class CartAdjustmentResult:
    subtotal: Decimal
    promo_discount: Decimal
    loyalty_points: int
    loyalty_discount: Decimal
    promo_code: str | None
    unit_prices: dict[int, Decimal] = field(default_factory=dict)

    @property
    def final_amount(self) -> Decimal:
        return max(
            self.subtotal - self.promo_discount - self.loyalty_discount,
            Decimal("0.00"),
        )


def _decimal_number(value: object, field: str) -> Decimal:
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=f"Invalid {field}") from exc
    if not amount.is_finite():
        raise HTTPException(status_code=409, detail=f"Invalid {field}")
    return amount


def _money(value: object, field: str) -> Decimal:
    return _decimal_number(value, field).quantize(_MONEY_STEP, rounding=ROUND_HALF_UP)


def _points(value: object, field: str) -> Decimal:
    return _decimal_number(value, field).quantize(_POINTS_STEP, rounding=ROUND_HALF_UP)


def _finite_non_negative(value: object, field: str, *, points: bool = False) -> Decimal:
    amount = _points(value, field) if points else _money(value, field)
    if amount < 0:
        raise HTTPException(status_code=409, detail=f"{field.capitalize()} cannot be negative")
    return amount


def _cart_subtotal(db: Session, cart: Cart) -> tuple[Decimal, dict[int, Decimal]]:
    products = []
    for item in cart.items:
        if not item.product:
            raise HTTPException(status_code=409, detail=f"Cart item {item.id} has no product")
        if isinstance(item.quantity, bool) or not isinstance(item.quantity, int) or item.quantity <= 0:
            raise HTTPException(status_code=409, detail=f"Cart item {item.id} has invalid quantity")
        products.append(item.product)

    pricing_now = utcnow_naive()
    quotes = load_product_price_quotes(db, products, now=pricing_now)
    unit_prices = {product_id: quote.effective_price for product_id, quote in quotes.items()}

    subtotal = Decimal("0.00")
    for item in cart.items:
        price = unit_prices.get(int(item.product_id))
        if price is None:
            raise HTTPException(status_code=409, detail=f"Missing price for cart item {item.id}")
        subtotal += price * item.quantity
    return subtotal.quantize(_MONEY_STEP, rounding=ROUND_HALF_UP), unit_prices


def _release_hold(hold: LoyaltyRedemptionHold | None) -> None:
    if not hold:
        return
    hold.status = "released"
    hold.released_at = utcnow_naive()


def _locked_loyalty_profile(db: Session, customer_id: int) -> CrmProfile | None:
    return (
        db.query(CrmProfile)
        .filter(CrmProfile.customer_id == customer_id)
        .with_for_update()
        .first()
    )


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
        discount = _money(calculate_discount(promo, subtotal), "promo discount")
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
        number = _points(value or 0, "loyalty points")
    except HTTPException:
        return 0
    if number <= 0 or number != number.to_integral_value():
        return 0
    return int(number)


def _strict_requested_points(value: object) -> int:
    try:
        number = _points(value, "loyalty points")
    except HTTPException as exc:
        raise HTTPException(status_code=422, detail="Invalid loyalty points") from exc
    if number < 0:
        raise HTTPException(status_code=422, detail="Loyalty points must be non-negative")
    if number != number.to_integral_value():
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
    available_points = max(profile_points - other_reserved, Decimal("0.0000"))
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
    # Canonical loyalty row order: CrmProfile -> LoyaltyRedemptionHold.
    # Checkout uses the same order; taking the profile first here prevents
    # cart-loyalty requests from holding a redemption row while waiting for
    # the customer's profile.
    profile = _locked_loyalty_profile(db, cart.customer_id)
    holds, current_hold = _reserved_holds(db, cart)
    requested_points = _whole_requested_points(cart.loyalty_points_to_redeem)
    if requested_points <= 0:
        cart.loyalty_points_to_redeem = Decimal("0.0000")
        _release_hold(current_hold)
        return 0, Decimal("0.00")

    if not profile:
        cart.loyalty_points_to_redeem = Decimal("0.0000")
        _release_hold(current_hold)
        return 0, Decimal("0.00")

    profile_points = _finite_non_negative(profile.loyalty_points, "loyalty balance", points=True)
    other_reserved = sum(
        (
            _finite_non_negative(hold.points, "reserved loyalty points", points=True)
            for hold in holds
            if hold.cart_id != cart.id
        ),
        Decimal("0.0000"),
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
        cart.loyalty_points_to_redeem = Decimal("0.0000")
        _release_hold(current_hold)
        return 0, Decimal("0.00")

    cart.loyalty_points_to_redeem = Decimal(effective_points).quantize(_POINTS_STEP)
    if current_hold:
        current_hold.points = Decimal(effective_points).quantize(_POINTS_STEP)
        current_hold.released_at = None
    else:
        db.add(
            LoyaltyRedemptionHold(
                customer_id=cart.customer_id,
                cart_id=cart.id,
                points=Decimal(effective_points).quantize(_POINTS_STEP),
                status="reserved",
            )
        )

    loyalty_discount = (Decimal(effective_points) * point_value).quantize(
        _MONEY_STEP,
        rounding=ROUND_HALF_UP,
    )
    return effective_points, loyalty_discount


def reconcile_cart_adjustments(db: Session, cart: Cart) -> CartAdjustmentResult:
    subtotal, unit_prices = _cart_subtotal(db, cart)
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
        unit_prices=unit_prices,
    )


def apply_loyalty_request(db: Session, cart: Cart, points: object) -> CartAdjustmentResult:
    requested_points = _strict_requested_points(points)
    baseline = reconcile_cart_adjustments(db, cart)

    # Baseline already followed profile -> hold. Preserve the same order for
    # the explicit-request validation pass so the transaction can never add a
    # late reverse edge.
    profile = _locked_loyalty_profile(db, cart.customer_id)
    holds, current_hold = _reserved_holds(db, cart)

    if requested_points == 0:
        cart.loyalty_points_to_redeem = Decimal("0.0000")
        _release_hold(current_hold)
        return CartAdjustmentResult(
            subtotal=baseline.subtotal,
            promo_discount=baseline.promo_discount,
            loyalty_points=0,
            loyalty_discount=Decimal("0.00"),
            promo_code=baseline.promo_code,
            unit_prices=baseline.unit_prices,
        )

    if not profile:
        raise HTTPException(status_code=409, detail="Loyalty profile not found")

    profile_points = _finite_non_negative(profile.loyalty_points, "loyalty balance", points=True)
    other_reserved = sum(
        (
            _finite_non_negative(hold.points, "reserved loyalty points", points=True)
            for hold in holds
            if hold.cart_id != cart.id
        ),
        Decimal("0.0000"),
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

    normalized_points = Decimal(requested_points).quantize(_POINTS_STEP)
    cart.loyalty_points_to_redeem = normalized_points
    if current_hold:
        current_hold.points = normalized_points
        current_hold.status = "reserved"
        current_hold.released_at = None
    else:
        db.add(
            LoyaltyRedemptionHold(
                customer_id=cart.customer_id,
                cart_id=cart.id,
                points=normalized_points,
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
        unit_prices=baseline.unit_prices,
    )
