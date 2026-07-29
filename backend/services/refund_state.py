"""Shared validation and state transitions for provider refunds."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import Order, ReturnRequest
from .refund_loyalty import apply_full_refund_loyalty

_MONEY_STEP = Decimal("0.01")


def refund_money(value: object, field: str) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(_MONEY_STEP, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"Invalid {field}")
    if not amount.is_finite():
        raise HTTPException(status_code=400, detail=f"Invalid {field}")
    return amount


def provider_refund_amount(provider_refund: dict, currency: str) -> Decimal:
    amount = provider_refund.get("amount") or {}
    provider_currency = str(amount.get("currency") or "").upper()
    provider_amount = refund_money(amount.get("value"), "provider refund amount")
    if provider_currency != str(currency).upper():
        raise HTTPException(status_code=409, detail="Provider refund currency does not match order")
    return provider_amount


def apply_provider_refund_status(
    db: Session,
    ret: ReturnRequest,
    order: Order,
    provider_status: str,
) -> dict[str, object]:
    normalized_status = provider_status.strip().lower()
    if normalized_status == "succeeded":
        full_refund = refund_money(ret.refund_amount, "refund amount") == refund_money(
            order.total_amount,
            "order total",
        )
        ret.status = "approved" if full_refund else "approved_partial"
        order.status = "refunded" if full_refund else "partially_refunded"
        order.payment_status = "refunded" if full_refund else "partially_refunded"
        if full_refund:
            return apply_full_refund_loyalty(
                db,
                customer_id=order.customer_id,
                order_id=order.id,
                redeemed_points=order.loyalty_points_redeemed,
            )
        return {"policy": "no_automatic_loyalty_adjustment_for_partial_refund"}

    if normalized_status == "canceled":
        ret.status = "failed"
        order.status = "refund_requested"
        order.payment_status = "paid"
        return {}

    ret.status = "refund_pending"
    order.status = "refund_requested"
    order.payment_status = "refund_pending"
    return {}
