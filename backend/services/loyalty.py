import random
import string
from sqlalchemy.orm import Session
from ..config import get_settings
from ..models import CrmProfile, LoyaltyTransaction, ReferralCode


def add_points(db: Session, customer_id: int, points: float, reason: str, order_id: int | None = None) -> None:
    db.add(LoyaltyTransaction(customer_id=customer_id, order_id=order_id, points_delta=points, reason=reason))
    profile = db.query(CrmProfile).filter(CrmProfile.customer_id == customer_id).first()
    if profile:
        profile.loyalty_points += points


def ensure_referral_code(db: Session, customer_id: int) -> ReferralCode:
    existing = db.query(ReferralCode).filter(ReferralCode.customer_id == customer_id, ReferralCode.active == True).first()
    if existing:
        return existing
    code = "FL" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    ref = ReferralCode(customer_id=customer_id, code=code)
    db.add(ref)
    db.commit()
    db.refresh(ref)
    return ref


def apply_referral(db: Session, code: str, new_customer_id: int) -> bool:
    ref = db.query(ReferralCode).filter(ReferralCode.code == code.strip().upper(), ReferralCode.active == True).first()
    if not ref or ref.customer_id == new_customer_id:
        return False
    add_points(db, ref.customer_id, ref.reward_points, "referral_reward")
    ref.used_count += 1
    return True



from datetime import datetime
from fastapi import HTTPException
from ..config import get_settings
from ..models import Cart, CrmProfile, ReferralAttribution


def redeem_points(db: Session, customer_id: int, cart: Cart, points: float) -> Cart:
    settings = get_settings()
    if points <= 0:
        cart.loyalty_points_to_redeem = 0
        return cart
    profile = db.query(CrmProfile).filter(CrmProfile.customer_id == customer_id).first()
    if not profile or profile.loyalty_points < points:
        raise HTTPException(status_code=409, detail="Not enough loyalty points")
    subtotal = sum(item.product.price * item.quantity for item in cart.items)
    max_discount = subtotal * settings.loyalty_max_redeem_percent / 100
    requested_discount = points * settings.loyalty_point_value_rub
    if requested_discount > max_discount:
        raise HTTPException(status_code=409, detail=f"Max loyalty redemption is {settings.loyalty_max_redeem_percent}% of cart")
    cart.loyalty_points_to_redeem = points
    return cart


def attach_referral_to_customer(db: Session, code: str, invited_customer_id: int) -> bool:
    ref = db.query(ReferralCode).filter(ReferralCode.code == code.strip().upper(), ReferralCode.active == True).first()
    if not ref or ref.customer_id == invited_customer_id:
        return False
    existing = db.query(ReferralAttribution).filter(ReferralAttribution.invited_customer_id == invited_customer_id).first()
    if existing:
        return False
    db.add(ReferralAttribution(referral_code_id=ref.id, invited_customer_id=invited_customer_id, status="pending"))
    return True


def reward_referral_after_first_paid_order(db: Session, invited_customer_id: int, order_id: int) -> None:
    attribution = (
        db.query(ReferralAttribution)
        .filter(ReferralAttribution.invited_customer_id == invited_customer_id, ReferralAttribution.status == "pending")
        .first()
    )
    if not attribution:
        return
    ref = db.query(ReferralCode).filter(ReferralCode.id == attribution.referral_code_id).first()
    if not ref:
        return
    add_points(db, ref.customer_id, ref.reward_points, "referral_reward", order_id)
    attribution.status = "rewarded"
    attribution.rewarded_order_id = order_id
    attribution.rewarded_at = datetime.utcnow()
    ref.used_count += 1



from datetime import datetime
from ..models import LoyaltyRedemptionHold


def create_redemption_hold(db: Session, customer_id: int, cart_id: int, points: float) -> LoyaltyRedemptionHold | None:
    if points <= 0:
        return None
    hold = db.query(LoyaltyRedemptionHold).filter(
        LoyaltyRedemptionHold.customer_id == customer_id,
        LoyaltyRedemptionHold.cart_id == cart_id,
        LoyaltyRedemptionHold.status == "reserved",
    ).first()
    if hold:
        hold.points = points
        return hold
    hold = LoyaltyRedemptionHold(customer_id=customer_id, cart_id=cart_id, points=points, status="reserved")
    db.add(hold)
    return hold


def mark_redemption_committed(db: Session, customer_id: int, cart_id: int | None, order_id: int, points: float) -> None:
    hold = None
    if cart_id:
        hold = db.query(LoyaltyRedemptionHold).filter(
            LoyaltyRedemptionHold.customer_id == customer_id,
            LoyaltyRedemptionHold.cart_id == cart_id,
            LoyaltyRedemptionHold.status == "reserved",
        ).first()
    if hold:
        hold.status = "committed"
        hold.order_id = order_id
    elif points > 0:
        db.add(LoyaltyRedemptionHold(customer_id=customer_id, order_id=order_id, points=points, status="committed"))


def refund_redeemed_points(db: Session, customer_id: int, order_id: int, points: float) -> None:
    if points <= 0:
        return
    existing = db.query(LoyaltyTransaction).filter(
        LoyaltyTransaction.customer_id == customer_id,
        LoyaltyTransaction.order_id == order_id,
        LoyaltyTransaction.reason == "loyalty_refund",
    ).first()
    if existing:
        return
    add_points(db, customer_id, points, "loyalty_refund", order_id)
    hold = db.query(LoyaltyRedemptionHold).filter(LoyaltyRedemptionHold.order_id == order_id).first()
    if hold:
        hold.status = "refunded"
        hold.released_at = datetime.utcnow()
