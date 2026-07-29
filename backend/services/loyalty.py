import hashlib
import random
import string
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    Cart,
    CrmProfile,
    LoyaltyRedemptionHold,
    LoyaltyTransaction,
    Order,
    Payment,
    ReferralAttribution,
    ReferralCode,
)

_POINT_STEP = Decimal("0.0001")
_MONEY_STEP = Decimal("0.01")
_RATE_STEP = Decimal("0.0001")
_HUNDRED = Decimal("100")
_MAX_POINTS = Decimal("9999999999999999.9999")
_MAX_MONEY = Decimal("999999999999999999.99")
_MAX_REWARD_RATE = Decimal("1000.0000")


def _decimal_value(value: object, field: str, step: Decimal, *, maximum: Decimal) -> Decimal:
    try:
        number = Decimal(str(value)).quantize(step, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"Invalid {field}")
    if not number.is_finite() or abs(number) > maximum:
        raise HTTPException(status_code=400, detail=f"Invalid {field}")
    return number


def _points(value: object, field: str = "loyalty points") -> Decimal:
    return _decimal_value(value, field, _POINT_STEP, maximum=_MAX_POINTS)


def _money(value: object, field: str) -> Decimal:
    return _decimal_value(value, field, _MONEY_STEP, maximum=_MAX_MONEY)


def _rate(value: object, field: str) -> Decimal:
    return _decimal_value(value, field, _RATE_STEP, maximum=_MAX_POINTS)


def _as_float(value: Decimal) -> float:
    return float(value)


def calculate_order_reward_points(total_amount: object, points_per_ruble: object | None = None) -> Decimal:
    total = _money(total_amount, "order total")
    if total < 0:
        raise HTTPException(status_code=409, detail="Order total cannot be negative")
    raw_rate = get_settings().loyalty_points_per_ruble if points_per_ruble is None else points_per_ruble
    reward_rate = _rate(raw_rate, "loyalty reward rate")
    if reward_rate < 0 or reward_rate > _MAX_REWARD_RATE:
        raise HTTPException(status_code=500, detail="Loyalty reward rate is misconfigured")
    return _points(total * reward_rate, "order reward points")


def _locked_profile(db: Session, customer_id: int, create: bool = False) -> CrmProfile | None:
    profile = (
        db.query(CrmProfile)
        .filter(CrmProfile.customer_id == customer_id)
        .with_for_update()
        .first()
    )
    if not profile and create:
        profile = CrmProfile(customer_id=customer_id, segment="new", loyalty_points=0)
        db.add(profile)
        db.flush()
    return profile


def add_points(
    db: Session,
    customer_id: int,
    points: float,
    reason: str,
    order_id: int | None = None,
) -> None:
    normalized_reason = (reason or "").strip()[:255]
    if normalized_reason == "order_paid":
        if not order_id:
            raise HTTPException(status_code=409, detail="Paid-order reward requires an order")
        order = db.query(Order).filter(Order.id == order_id).with_for_update().first()
        if not order or order.customer_id != customer_id:
            raise HTTPException(status_code=409, detail="Paid-order reward does not match customer")
        successful_payment = (
            db.query(Payment)
            .filter(Payment.order_id == order_id, Payment.status == "succeeded")
            .with_for_update()
            .first()
        )
        if not successful_payment:
            raise HTTPException(status_code=409, detail="Paid-order reward requires a successful payment")
        points_value = calculate_order_reward_points(order.total_amount)
    else:
        points_value = _points(points)

    if points_value == 0:
        return

    profile = _locked_profile(db, customer_id, create=True)
    new_balance = _points(_points(profile.loyalty_points, "loyalty balance") + points_value, "loyalty balance")
    if new_balance < 0:
        raise HTTPException(status_code=409, detail="Loyalty balance cannot become negative")

    db.add(
        LoyaltyTransaction(
            customer_id=customer_id,
            order_id=order_id,
            points_delta=_as_float(points_value),
            reason=normalized_reason,
        )
    )
    profile.loyalty_points = _as_float(new_balance)


def _referral_lock_key(customer_id: int) -> int:
    digest = hashlib.sha256(f"referral-code:{customer_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _acquire_referral_lock(db: Session, customer_id: int) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": _referral_lock_key(customer_id)})


def ensure_referral_code(db: Session, customer_id: int) -> ReferralCode:
    _acquire_referral_lock(db, customer_id)
    existing = (
        db.query(ReferralCode)
        .filter(ReferralCode.customer_id == customer_id, ReferralCode.active.is_(True))
        .with_for_update()
        .first()
    )
    if existing:
        return existing

    for _ in range(10):
        code = "FL" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        try:
            with db.begin_nested():
                referral = ReferralCode(customer_id=customer_id, code=code)
                db.add(referral)
                db.flush()
            return referral
        except IntegrityError:
            continue
    raise HTTPException(status_code=503, detail="Could not generate referral code")


def apply_referral(db: Session, code: str, new_customer_id: int) -> bool:
    normalized_code = (code or "").strip().upper()
    referral = (
        db.query(ReferralCode)
        .filter(ReferralCode.code == normalized_code, ReferralCode.active.is_(True))
        .with_for_update()
        .first()
    )
    if not referral or referral.customer_id == new_customer_id:
        return False
    add_points(db, referral.customer_id, referral.reward_points, "referral_reward")
    referral.used_count += 1
    return True


def redeem_points(db: Session, customer_id: int, cart: Cart, points: float) -> Cart:
    settings = get_settings()
    requested_points = _points(points)
    holds = (
        db.query(LoyaltyRedemptionHold)
        .filter(
            LoyaltyRedemptionHold.customer_id == customer_id,
            LoyaltyRedemptionHold.status == "reserved",
        )
        .with_for_update()
        .all()
    )
    current_hold = next((hold for hold in holds if hold.cart_id == cart.id), None)

    if requested_points <= 0:
        cart.loyalty_points_to_redeem = 0
        if current_hold:
            current_hold.status = "released"
            current_hold.released_at = datetime.utcnow()
        return cart

    profile = _locked_profile(db, customer_id)
    if not profile:
        raise HTTPException(status_code=409, detail="Loyalty profile not found")

    other_reserved = sum(
        (_points(hold.points, "reserved loyalty points") for hold in holds if hold.cart_id != cart.id),
        Decimal("0"),
    )
    available_points = _points(
        _points(profile.loyalty_points, "loyalty balance") - other_reserved,
        "available loyalty points",
    )
    if requested_points > available_points:
        raise HTTPException(status_code=409, detail="Not enough available loyalty points")

    subtotal = _money(
        sum((_money(item.product.price, "product price") * int(item.quantity) for item in cart.items), Decimal("0")),
        "cart subtotal",
    )
    loyalty_limit = _rate(settings.loyalty_max_redeem_percent, "loyalty limit")
    if loyalty_limit < 0 or loyalty_limit > _HUNDRED:
        raise HTTPException(status_code=500, detail="Loyalty limit is misconfigured")
    point_value = _money(settings.loyalty_point_value_rub, "loyalty point value")
    if point_value <= 0:
        raise HTTPException(status_code=500, detail="Loyalty point value is misconfigured")

    max_discount = _money(subtotal * loyalty_limit / _HUNDRED, "maximum loyalty discount")
    requested_discount = _money(requested_points * point_value, "requested loyalty discount")
    if requested_discount > max_discount:
        raise HTTPException(
            status_code=409,
            detail=f"Max loyalty redemption is {settings.loyalty_max_redeem_percent}% of cart",
        )

    cart.loyalty_points_to_redeem = _as_float(requested_points)
    if current_hold:
        current_hold.points = _as_float(requested_points)
    else:
        db.add(
            LoyaltyRedemptionHold(
                customer_id=customer_id,
                cart_id=cart.id,
                points=_as_float(requested_points),
                status="reserved",
            )
        )
    return cart


def attach_referral_to_customer(db: Session, code: str, invited_customer_id: int) -> bool:
    normalized_code = (code or "").strip().upper()
    referral = (
        db.query(ReferralCode)
        .filter(ReferralCode.code == normalized_code, ReferralCode.active.is_(True))
        .first()
    )
    if not referral or referral.customer_id == invited_customer_id:
        return False

    existing = (
        db.query(ReferralAttribution)
        .filter(ReferralAttribution.invited_customer_id == invited_customer_id)
        .with_for_update()
        .first()
    )
    if existing:
        return False
    db.add(
        ReferralAttribution(
            referral_code_id=referral.id,
            invited_customer_id=invited_customer_id,
            status="pending",
        )
    )
    return True


def reward_referral_after_first_paid_order(db: Session, invited_customer_id: int, order_id: int) -> None:
    attribution = (
        db.query(ReferralAttribution)
        .filter(
            ReferralAttribution.invited_customer_id == invited_customer_id,
            ReferralAttribution.status == "pending",
        )
        .with_for_update()
        .first()
    )
    if not attribution:
        return

    referral = (
        db.query(ReferralCode)
        .filter(ReferralCode.id == attribution.referral_code_id, ReferralCode.active.is_(True))
        .with_for_update()
        .first()
    )
    if not referral:
        return

    add_points(db, referral.customer_id, referral.reward_points, "referral_reward", order_id)
    attribution.status = "rewarded"
    attribution.rewarded_order_id = order_id
    attribution.rewarded_at = datetime.utcnow()
    referral.used_count += 1


def create_redemption_hold(
    db: Session,
    customer_id: int,
    cart_id: int,
    points: float,
) -> LoyaltyRedemptionHold | None:
    points_value = _points(points)
    hold = (
        db.query(LoyaltyRedemptionHold)
        .filter(
            LoyaltyRedemptionHold.customer_id == customer_id,
            LoyaltyRedemptionHold.cart_id == cart_id,
            LoyaltyRedemptionHold.status == "reserved",
        )
        .with_for_update()
        .first()
    )
    if points_value <= 0:
        if hold:
            hold.status = "released"
            hold.released_at = datetime.utcnow()
        return None
    if hold:
        hold.points = _as_float(points_value)
        return hold

    hold = LoyaltyRedemptionHold(
        customer_id=customer_id,
        cart_id=cart_id,
        points=_as_float(points_value),
        status="reserved",
    )
    db.add(hold)
    return hold


def mark_redemption_committed(
    db: Session,
    customer_id: int,
    cart_id: int | None,
    order_id: int,
    points: float,
) -> None:
    points_value = _points(points)
    if points_value <= 0:
        return

    committed = (
        db.query(LoyaltyRedemptionHold)
        .filter(
            LoyaltyRedemptionHold.customer_id == customer_id,
            LoyaltyRedemptionHold.order_id == order_id,
            LoyaltyRedemptionHold.status == "committed",
        )
        .with_for_update()
        .first()
    )
    if committed:
        return

    query = db.query(LoyaltyRedemptionHold).filter(
        LoyaltyRedemptionHold.customer_id == customer_id,
        LoyaltyRedemptionHold.status == "reserved",
    )
    if cart_id is not None:
        query = query.filter(LoyaltyRedemptionHold.cart_id == cart_id)
    else:
        query = query.filter(LoyaltyRedemptionHold.order_id == order_id)
    hold = query.with_for_update().first()

    if hold:
        hold.status = "committed"
        hold.order_id = order_id
        hold.points = _as_float(points_value)
        return

    db.add(
        LoyaltyRedemptionHold(
            customer_id=customer_id,
            cart_id=cart_id,
            order_id=order_id,
            points=_as_float(points_value),
            status="committed",
        )
    )


def refund_redeemed_points(db: Session, customer_id: int, order_id: int, points: float) -> None:
    points_value = _points(points)
    if points_value <= 0:
        return

    existing = (
        db.query(LoyaltyTransaction)
        .filter(
            LoyaltyTransaction.customer_id == customer_id,
            LoyaltyTransaction.order_id == order_id,
            LoyaltyTransaction.reason == "loyalty_refund",
        )
        .with_for_update()
        .first()
    )
    if existing:
        return

    hold = (
        db.query(LoyaltyRedemptionHold)
        .filter(
            LoyaltyRedemptionHold.customer_id == customer_id,
            LoyaltyRedemptionHold.order_id == order_id,
        )
        .with_for_update()
        .first()
    )
    if hold and hold.status == "refunded":
        return

    add_points(db, customer_id, _as_float(points_value), "loyalty_refund", order_id)
    if hold:
        hold.status = "refunded"
        hold.released_at = datetime.utcnow()
