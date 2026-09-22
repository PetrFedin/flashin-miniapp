from __future__ import annotations

from sqlalchemy.orm import Session

from ..services.refund_locking import lock_return_request_for_known_order

_FINAL_STATUSES = {"approved", "approved_partial", "failed"}


def mark_refund_review_required(
    db: Session,
    *,
    return_id: int,
    order_id: int,
) -> bool:
    """Persist review using canonical Order -> ReturnRequest locking."""

    order, ret = lock_return_request_for_known_order(db, return_id, order_id)
    if not ret or not order or ret.status in _FINAL_STATUSES:
        db.rollback()
        return False

    ret.status = "refund_review_required"
    order.status = "refund_requested"
    order.payment_status = "refund_review_required"
    db.commit()
    return True
