"""Idempotent loyalty adjustments for a fully refunded order."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy.orm import Session

from ..models import (
    CrmProfile,
    LoyaltyTransaction,
    ReferralAttribution,
    ReferralCode,
)
from .loyalty import refund_redeemed_points

_POINTS_STEP = Decimal("0.0001")
_ZERO_POINTS = Decimal("0.0000")


def _points(value) -> Decimal:
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Invalid loyalty points") from exc
    if not amount.is_finite():
        raise ValueError("Invalid loyalty points")
    return amount.quantize(_POINTS_STEP, rounding=ROUND_HALF_UP)


def _public_points(value: Decimal) -> float:
    return float(value.quantize(_POINTS_STEP, rounding=ROUND_HALF_UP))


def _locked_referral_reward_root(
    db: Session,
    *,
    order_id: int,
) -> tuple[ReferralAttribution | None, ReferralCode | None]:
    """Lock a rewarded-order referral root before any loyalty profile mutation.

    Referral settlement already serializes the reusable referrer identity as
    ReferralAttribution -> ReferralCode -> CrmProfile. A full refund must never
    acquire the same referrer's CrmProfile before its ReferralCode or two
    different invited orders using one code can deadlock each other.
    """

    attribution = (
        db.query(ReferralAttribution)
        .filter(ReferralAttribution.rewarded_order_id == order_id)
        .with_for_update()
        .first()
    )
    if not attribution:
        return None, None

    referral = (
        db.query(ReferralCode)
        .filter(ReferralCode.id == attribution.referral_code_id)
        .with_for_update()
        .first()
    )
    return attribution, referral


def _reverse_transaction(
    db: Session,
    *,
    customer_id: int,
    order_id: int,
    original_reason: str,
    reversal_reason: str,
) -> dict[str, float | bool]:
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
    target = max(_points(original.points_delta), _ZERO_POINTS) if original else _ZERO_POINTS

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
        reversed_points = abs(_points(existing.points_delta))
        unrecovered = max(target - reversed_points, _ZERO_POINTS).quantize(
            _POINTS_STEP,
            rounding=ROUND_HALF_UP,
        )
        return {
            "target": _public_points(target),
            "reversed": _public_points(reversed_points),
            "unrecovered": _public_points(unrecovered),
            "idempotent": True,
        }
    if target <= 0:
        return {
            "target": 0.0,
            "reversed": 0.0,
            "unrecovered": 0.0,
            "idempotent": False,
        }

    profile = (
        db.query(CrmProfile)
        .filter(CrmProfile.customer_id == customer_id)
        .with_for_update()
        .first()
    )
    if not profile:
        profile = CrmProfile(customer_id=customer_id, segment="new", loyalty_points=_ZERO_POINTS)
        db.add(profile)
        db.flush()

    balance = max(_points(profile.loyalty_points), _ZERO_POINTS)
    reversed_points = min(balance, target).quantize(_POINTS_STEP, rounding=ROUND_HALF_UP)
    unrecovered = max(target - reversed_points, _ZERO_POINTS).quantize(
        _POINTS_STEP,
        rounding=ROUND_HALF_UP,
    )

    db.add(
        LoyaltyTransaction(
            customer_id=customer_id,
            order_id=order_id,
            points_delta=-reversed_points,
            reason=reversal_reason,
        )
    )
    profile.loyalty_points = (balance - reversed_points).quantize(
        _POINTS_STEP,
        rounding=ROUND_HALF_UP,
    )
    return {
        "target": _public_points(target),
        "reversed": _public_points(reversed_points),
        "unrecovered": _public_points(unrecovered),
        "idempotent": False,
    }


def apply_full_refund_loyalty(
    db: Session,
    *,
    customer_id: int,
    order_id: int,
    redeemed_points,
) -> dict[str, object]:
    """Reverse earned rewards first, then restore redeemed points.

    This ordering prevents previously spent reward points from consuming the
    customer's restored redemption balance. Any reward that cannot be recovered
    is reported to the admin audit payload instead of pushing the balance below zero.

    If the order was referral-rewarded, its attribution and reusable referral
    code are locked before any profile balance mutation. This matches payment
    settlement's ReferralCode -> CrmProfile order across different invited
    orders that share one referrer.
    """

    attribution, referral = _locked_referral_reward_root(db, order_id=order_id)

    customer_reward = _reverse_transaction(
        db,
        customer_id=customer_id,
        order_id=order_id,
        original_reason="order_paid",
        reversal_reason="order_refund_reversal",
    )

    referral_adjustments: list[dict[str, float | int | bool]] = []
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

    if attribution and attribution.status == "rewarded":
        attribution.status = "reversed"
        if referral:
            referral.used_count = max(int(referral.used_count or 0) - 1, 0)

    refund_redeemed_points(db, customer_id, order_id, redeemed_points)

    restored_points = max(_points(redeemed_points), _ZERO_POINTS)
    return {
        "redeemed_points_restored": _public_points(restored_points),
        "customer_reward": customer_reward,
        "referral_rewards": referral_adjustments,
    }
