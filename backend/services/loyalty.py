import math
import random
import string

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import utcnow_naive
from ..models import (
    Cart,
    CrmProfile,
    LoyaltyRedemptionHold,
    LoyaltyTransaction,
    ReferralAttribution,
    ReferralCode,
)


def _finite_number(value: float, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"Invalid {field}")
    if not math.isfinite(number):
        raise HTTPException(status_code=400, detail=f"Invalid {field}")
    return number


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
    points_value = _finite_number(points, "loyalty points")
    if points_value == 0:
        return

    profile = _locked_profile(db, customer_id, create=True)
    new_balance = _finite_number(profile.loyalty_points, "loyalty balance") + points_value
    if new_balance < 0:
        raise HTTPException(status_code=409, detail="Loyalty balance cannot become negative")

    db.add(
        LoyaltyTransaction(
            customer_id=customer_id,
            order_id=order_id,
            points_delta=points_value,
            reason=(reason or "").strip()[:255],
        )
    )
    profile.loyalty_points = new_balance


def ensure_referral_code(db: Session, customer_id: int) -> ReferralCode:
    existing = (
        db.query(ReferralCode)
        .filter(ReferralCode.customer_id == customer_id, ReferralCode.active.is_(True))
        .first()
    )
    if existing:
        return existing

    for _ in range(5):
        code = "FL" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        if not db.query(ReferralCode.id).filter(ReferralCode.code == code).first():
            referral = ReferralCode(customer_id=customer_id, code=code)
            db.add(referral)
            db.commit()
            db.refresh(referral)
            return referral
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
    requested_points = _finite_number(points, "loyalty points")
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
            current_hold.released_at = utcnow_naive()
        return cart

    profile = _locked_profile(db, customer_id)
    if not profile:
        raise HTTPException(status_code=409, detail="Loyalty profile not found")

    other_reserved = sum(
        _finite_number(hold.points, "reserved loyalty points")
        for hold in holds
        if hold.cart_id != cart.id
    )
    available_points = _finite_number(profile.loyalty_points, "loyalty balance") - other_reserved
    if requested_points > available_points:
        raise HTTPException(status_code=409, detail="Not enough available loyalty points")

    subtotal = sum(
        _finite_number(item.product.price, "product price") * item.quantity
        for item in cart.items
    )
    max_discount = subtotal * _finite_number(settings.loyalty_max_redeem_percent, "loyalty limit") / 100
    requested_discount = requested_points * _finite_number(settings.loyalty_point_value_rub, "loyalty point value")
    if requested_discount > max_discount:
        raise HTTPException(
            status_code=409,
            detail=f"Max loyalty redemption is {settings.loyalty_max_redeem_percent}% of cart",
        )

    cart.loyalty_points_to_redeem = requested_points
    if current_hold:
        current_hold.points = requested_points
    else:
        db.add(
            LoyaltyRedemptionHold(
                customer_id=customer_id,
                cart_id=cart.id,
                points=requested_points,
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
    attribution.rewarded_at = utcnow_naive()
    referral.used_count += 1


def create_redemption_hold(
    db: Session,
    customer_id: int,
    cart_id: int,
    points: float,
) -> LoyaltyRedemptionHold | None:
    points_value = _finite_number(points, "loyalty points")
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
            hold.released_at = utcnow_naive()
        return None
    if hold:
        hold.points = points_value
        return hold

    hold = LoyaltyRedemptionHold(
        customer_id=customer_id,
        cart_id=cart_id,
        points=points_value,
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
    points_value = _finite_number(points, "loyalty points")
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
        hold.points = points_value
        return

    db.add(
        LoyaltyRedemptionHold(
            customer_id=customer_id,
            cart_id=cart_id,
            order_id=order_id,
            points=points_value,
            status="committed",
        )
    )


def refund_redeemed_points(db: Session, customer_id: int, order_id: int, points: float) -> None:
    points_value = _finite_number(points, "loyalty points")
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

    add_points(db, customer_id, points_value, "loyalty_refund", order_id)
    if hold:
        hold.status = "refunded"
        hold.released_at = utcnow_naive()
