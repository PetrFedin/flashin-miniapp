from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..models import Order, Payment, PaymentReconciliation, ReturnRequest


_ORDER_PAYMENT_REVIEW_STATUSES = (
    "paid_review_required",
    "payment_review_required",
    "refund_retry_required",
    "refund_review_required",
)
_PAYMENT_RECORD_REVIEW_STATUSES = (
    "paid_review_required",
    "refund_retry_required",
    "refund_review_required",
)
_REFUND_ATTENTION_STATUSES = (
    "refund_retry_required",
    "refund_review_required",
)


def _normalized_order_ids(order_ids: Iterable[int] | None) -> list[int]:
    result: set[int] = set()
    for value in order_ids or ():
        try:
            order_id = int(value)
        except (TypeError, ValueError):
            continue
        if order_id > 0:
            result.add(order_id)
    return sorted(result)


def build_pilot_money_safety(
    db: Session,
    order_ids: Iterable[int] | None,
) -> dict[str, Any]:
    """Return one sanitized, deterministic money-safety verdict for pilot orders.

    This service intentionally returns counts and bounded reason codes only. Raw
    provider payloads, error text, payment identifiers and customer data must not
    cross the pilot admission boundary.
    """

    ids = _normalized_order_ids(order_ids)
    if not ids:
        return {
            "healthy": True,
            "attention_required": False,
            "payment_review_orders": 0,
            "refund_attention_orders": 0,
            "reconciliation_mismatches": 0,
            "blocking_codes": [],
            "stop_reason": None,
        }

    payment_review_orders = int(
        db.query(func.count(func.distinct(Order.id)))
        .outerjoin(Payment, Payment.order_id == Order.id)
        .filter(
            Order.id.in_(ids),
            or_(
                Order.status == "payment_review_required",
                Order.payment_status.in_(_ORDER_PAYMENT_REVIEW_STATUSES),
                Payment.status.in_(_PAYMENT_RECORD_REVIEW_STATUSES),
            ),
        )
        .scalar()
        or 0
    )

    refund_retry_orders = int(
        db.query(func.count(func.distinct(ReturnRequest.order_id)))
        .filter(
            ReturnRequest.order_id.in_(ids),
            ReturnRequest.status == "refund_retry_required",
        )
        .scalar()
        or 0
    )
    refund_review_orders = int(
        db.query(func.count(func.distinct(ReturnRequest.order_id)))
        .filter(
            ReturnRequest.order_id.in_(ids),
            ReturnRequest.status == "refund_review_required",
        )
        .scalar()
        or 0
    )
    refund_attention_orders = refund_retry_orders + refund_review_orders

    reconciliation_mismatches = int(
        db.query(func.count(PaymentReconciliation.id))
        .filter(
            PaymentReconciliation.order_id.in_(ids),
            PaymentReconciliation.status == "mismatch",
            PaymentReconciliation.resolved_at.is_(None),
        )
        .scalar()
        or 0
    )

    blocking_codes: list[str] = []
    if payment_review_orders:
        blocking_codes.append("pilot_payment_review_required")
    if refund_attention_orders:
        blocking_codes.append("pilot_refund_attention_required")
    if reconciliation_mismatches:
        blocking_codes.append("pilot_payment_reconciliation_mismatch")

    stop_reason: str | None = None
    if payment_review_orders:
        stop_reason = "payment_review_required"
    elif reconciliation_mismatches:
        stop_reason = "payment_reconciliation_mismatch"
    elif refund_review_orders:
        stop_reason = "refund_review_required"
    elif refund_retry_orders:
        stop_reason = "refund_retry_required"

    attention_required = bool(blocking_codes)
    return {
        "healthy": not attention_required,
        "attention_required": attention_required,
        "payment_review_orders": payment_review_orders,
        "refund_attention_orders": refund_attention_orders,
        "reconciliation_mismatches": reconciliation_mismatches,
        "blocking_codes": blocking_codes,
        "stop_reason": stop_reason,
    }
