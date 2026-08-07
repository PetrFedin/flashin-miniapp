"""Shared validation and state transitions for provider refunds."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import Order, ReturnRequest
from .inventory import restore_sold_variants
from .moysklad_outbound import enqueue_moysklad_sales_return
from .notifications import queue_order_refund
from .refund_loyalty import apply_full_refund_loyalty

_MONEY_STEP = Decimal("0.01")
_FINAL_REFUND_STATUSES = {"approved", "approved_partial"}


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


def completed_refund_total(
    db: Session,
    order_id: int,
    *,
    exclude_return_id: int | None = None,
) -> Decimal:
    query = db.query(ReturnRequest).filter(
        ReturnRequest.order_id == order_id,
        ReturnRequest.status.in_(_FINAL_REFUND_STATUSES),
    )
    if exclude_return_id is not None:
        query = query.filter(ReturnRequest.id != exclude_return_id)
    rows = query.with_for_update().all()
    return sum(
        (refund_money(row.refund_amount, "completed refund amount") for row in rows),
        Decimal("0.00"),
    ).quantize(_MONEY_STEP, rounding=ROUND_HALF_UP)


def remaining_refundable_amount(
    db: Session,
    order: Order,
    *,
    exclude_return_id: int | None = None,
) -> Decimal:
    order_total = refund_money(order.total_amount, "order total")
    completed = completed_refund_total(
        db,
        order.id,
        exclude_return_id=exclude_return_id,
    )
    remaining = (order_total - completed).quantize(_MONEY_STEP, rounding=ROUND_HALF_UP)
    if remaining < 0:
        raise HTTPException(status_code=409, detail="Completed refunds exceed order total")
    return remaining


def _order_item_quantities(order: Order) -> dict[int, int]:
    quantities: dict[int, int] = {}
    for item in order.items:
        quantities[item.variant_id] = quantities.get(item.variant_id, 0) + item.quantity
    if not quantities:
        raise HTTPException(status_code=409, detail="Refunded order has no inventory items")
    return quantities


def _idempotent_succeeded_result(db: Session, ret: ReturnRequest, order: Order) -> dict[str, object]:
    order_total = refund_money(order.total_amount, "order total")
    cumulative_total = completed_refund_total(db, order.id)
    if cumulative_total > order_total:
        raise HTTPException(status_code=409, detail="Cumulative refunds exceed order total")
    return {
        "cumulative_refund_amount": float(cumulative_total),
        "remaining_refundable_amount": float(order_total - cumulative_total),
        "idempotent": True,
        "return_status": ret.status,
    }


def apply_provider_refund_status(
    db: Session,
    ret: ReturnRequest,
    order: Order,
    provider_status: str,
) -> dict[str, object]:
    normalized_status = provider_status.strip().lower()
    if normalized_status == "succeeded":
        if ret.status in _FINAL_REFUND_STATUSES:
            return _idempotent_succeeded_result(db, ret, order)

        order_total = refund_money(order.total_amount, "order total")
        previous_total = completed_refund_total(
            db,
            order.id,
            exclude_return_id=ret.id,
        )
        current_amount = refund_money(ret.refund_amount, "refund amount")
        cumulative_total = (previous_total + current_amount).quantize(
            _MONEY_STEP,
            rounding=ROUND_HALF_UP,
        )
        if cumulative_total > order_total:
            raise HTTPException(status_code=409, detail="Cumulative refunds exceed order total")

        full_refund = cumulative_total == order_total
        ret.status = "approved" if full_refund else "approved_partial"
        order.status = "refunded" if full_refund else "partially_refunded"
        order.payment_status = "refunded" if full_refund else "partially_refunded"
        result: dict[str, object] = {
            "cumulative_refund_amount": float(cumulative_total),
            "remaining_refundable_amount": float(order_total - cumulative_total),
        }

        if full_refund:
            result.update(
                apply_full_refund_loyalty(
                    db,
                    customer_id=order.customer_id,
                    order_id=order.id,
                    redeemed_points=order.loyalty_points_redeemed,
                )
            )
            restored = restore_sold_variants(
                db,
                _order_item_quantities(order),
                order_id=order.id,
                source="full_refund",
            )
            result["inventory_restored"] = restored
            if order.delivery_status in {"shipped", "delivered"}:
                enqueue_moysklad_sales_return(db, order.id, ret.id)
        else:
            result["policy"] = "loyalty_and_inventory_adjusted_only_after_full_cumulative_refund"

        queue_order_refund(
            db,
            order,
            return_id=ret.id,
            amount=float(current_amount),
            full_refund=full_refund,
        )
        return result

    if normalized_status == "canceled":
        ret.status = "failed"
        remaining = remaining_refundable_amount(db, order, exclude_return_id=ret.id)
        if remaining < refund_money(order.total_amount, "order total"):
            order.status = "partially_refunded"
            order.payment_status = "partially_refunded"
        else:
            order.status = "refund_requested"
            order.payment_status = "paid"
        return {}

    ret.status = "refund_pending"
    order.status = "refund_requested"
    order.payment_status = "refund_pending"
    return {}
