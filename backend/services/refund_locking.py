from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import Order, ReturnRequest


def _load_return_request_order_id(db: Session, return_id: int) -> int | None:
    row = (
        db.query(ReturnRequest.order_id)
        .filter(ReturnRequest.id == return_id)
        .first()
    )
    return int(row[0]) if row else None


def _load_provider_refund_order_id(db: Session, provider_refund_id: str) -> int | None:
    row = (
        db.query(ReturnRequest.order_id)
        .filter(ReturnRequest.provider_refund_id == provider_refund_id)
        .first()
    )
    return int(row[0]) if row else None


def _select_refund_order_for_update(db: Session, order_id: int) -> Order | None:
    return db.query(Order).filter(Order.id == order_id).with_for_update().first()


def _select_return_request_for_update(db: Session, return_id: int) -> ReturnRequest | None:
    return (
        db.query(ReturnRequest)
        .filter(ReturnRequest.id == return_id)
        .with_for_update()
        .first()
    )


def _select_provider_return_request_for_update(
    db: Session,
    provider_refund_id: str,
) -> ReturnRequest | None:
    return (
        db.query(ReturnRequest)
        .filter(ReturnRequest.provider_refund_id == provider_refund_id)
        .with_for_update()
        .first()
    )


def lock_return_request_for_approval(
    db: Session,
    return_id: int,
) -> tuple[Order, ReturnRequest]:
    """Lock an approval root in canonical Order -> ReturnRequest order.

    The initial non-locking order-id snapshot exists only to discover the lock
    root. The relationship is revalidated after both rows are locked so a
    concurrent/corrupt reassignment fails closed instead of allowing an inverse
    ReturnRequest -> Order lock sequence.
    """

    expected_order_id = _load_return_request_order_id(db, return_id)
    if expected_order_id is None:
        raise HTTPException(status_code=404, detail="Return request not found")

    order = _select_refund_order_for_update(db, expected_order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    ret = _select_return_request_for_update(db, return_id)
    if not ret:
        raise HTTPException(status_code=404, detail="Return request not found")
    if int(ret.order_id) != expected_order_id:
        raise HTTPException(
            status_code=409,
            detail="Return request changed during refund locking",
        )
    return order, ret


def lock_return_request_for_known_order(
    db: Session,
    return_id: int,
    order_id: int,
) -> tuple[Order | None, ReturnRequest | None]:
    """Reacquire known refund rows as Order -> ReturnRequest.

    Recovery callers historically tolerate a missing row, so this helper keeps
    those semantics and only rejects a return that moved to another order.
    """

    order = _select_refund_order_for_update(db, order_id)
    ret = _select_return_request_for_update(db, return_id)
    if ret and int(ret.order_id) != int(order_id):
        raise HTTPException(
            status_code=409,
            detail="Return request changed during refund locking",
        )
    return order, ret


def lock_return_request_for_provider_refund(
    db: Session,
    provider_refund_id: str,
) -> tuple[Order | None, ReturnRequest | None]:
    """Resolve a provider refund, then lock Order before ReturnRequest."""

    expected_order_id = _load_provider_refund_order_id(db, provider_refund_id)
    if expected_order_id is None:
        return None, None

    order = _select_refund_order_for_update(db, expected_order_id)
    ret = _select_provider_return_request_for_update(db, provider_refund_id)
    if not ret:
        return order, None
    if int(ret.order_id) != expected_order_id:
        raise HTTPException(
            status_code=409,
            detail="Provider refund binding changed during refund locking",
        )
    return order, ret
