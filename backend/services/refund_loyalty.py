"""Idempotent loyalty adjustments for a fully refunded order."""

from sqlalchemy.orm import Session

from ..models import (
    CrmProfile,
    LoyaltyTransaction,
    ReferralAttribution,
    ReferralCode,
)
from .loyalty import refund_redeemed_points


def _reverse_transaction(
    db: Session,
    *,
    customer_id: int,
    order_id: int,
    original_reason: str,
    reversal_reason: str,
) -> dict[str, float]:
    existing = (
        db.query(LoyaltyTransaction)
        .filter(
            LoyaltyTransaction.customer_id == customer_id,
            LoyaltyTransaction.order_id == order_id,
            LoyaltyTransaction.reason == reversal_reason,
        )
        .with_for_update()
        .first()
    )
    if existing:
        reversed_points = abs(float(existing.points_delta or 0))
        return {
            "target": reversed_points,
            "reversed": reversed_points,
            "unrecovered": 0.0,
            "idempotent": 1.0,
        }

    original = (
        db.query(LoyaltyTransaction)
        .filter(
            LoyaltyTransaction.customer_id == customer_id,
            LoyaltyTransaction.order_id == order_id,
            LoyaltyTransaction.reason == original_reason,
        )
        .with_for_update()
        .first()
    )
    if not original or float(original.points_delta or 0) <= 0:
        return {"target": 0.0, "reversed": 0.0, "unrecovered": 0.0, "idempotent": 0.0}

    profile = (
        db.query(CrmProfile)
        .filter(CrmProfile.customer_id == customer_id)
        .with_for_update()
        .first()
    )
    if not profile:
        profile = CrmProfile(customer_id=customer_id, segment="new", loyalty_points=0)
        db.add(profile)
        db.flush()

    target = round(float(original.points_delta), 2)
    balance = max(float(profile.loyalty_points or 0), 0.0)
    reversed_points = round(min(balance, target), 2)
    unrecovered = round(max(target - reversed_points, 0.0), 2)

    db.add(
        LoyaltyTransaction(
            customer_id=customer_id,
            order_id=order_id,
            points_delta=-reversed_points,
            reason=reversal_reason,
        )
    )
    profile.loyalty_points = round(balance - reversed_points, 2)
    return {
        "target": target,
        "reversed": reversed_points,
        "unrecovered": unrecovered,
        "idempotent": 0.0,
    }


def apply_full_refund_loyalty(
    db: Session,
    *,
    customer_id: int,
    order_id: int,
    redeemed_points: float,
) -> dict[str, object]:
    """Restore redeemed points and reverse rewards without allowing a negative balance."""

    refund_redeemed_points(db, customer_id, order_id, redeemed_points)
    customer_reward = _reverse_transaction(
        db,
        customer_id=customer_id,
        order_id=order_id,
        original_reason="order_paid",
        reversal_reason="order_refund_reversal",
    )

    referral_adjustments: list[dict[str, float | int]] = []
    referral_rewards = (
        db.query(LoyaltyTransaction)
        .filter(
            LoyaltyTransaction.order_id == order_id,
            LoyaltyTransaction.reason == "referral_reward",
        )
        .with_for_update()
        .all()
    )
    for reward in referral_rewards:
        adjustment = _reverse_transaction(
            db,
            customer_id=reward.customer_id,
            order_id=order_id,
            original_reason="referral_reward",
            reversal_reason="referral_refund_reversal",
        )
        referral_adjustments.append({"customer_id": reward.customer_id, **adjustment})

    attribution = (
        db.query(ReferralAttribution)
        .filter(ReferralAttribution.rewarded_order_id == order_id)
        .with_for_update()
        .first()
    )
    if attribution and attribution.status == "rewarded":
        referral = (
            db.query(ReferralCode)
            .filter(ReferralCode.id == attribution.referral_code_id)
            .with_for_update()
            .first()
        )
        attribution.status = "reversed"
        if referral:
            referral.used_count = max(int(referral.used_count or 0) - 1, 0)

    return {
        "redeemed_points_restored": round(max(float(redeemed_points or 0), 0.0), 2),
        "customer_reward": customer_reward,
        "referral_rewards": referral_adjustments,
    }
