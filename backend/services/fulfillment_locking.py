from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import FulfillmentTask, Order


def _load_fulfillment_order_id(db: Session, task_id: int) -> int | None:
    row = (
        db.query(FulfillmentTask.order_id)
        .filter(FulfillmentTask.id == task_id)
        .first()
    )
    return int(row[0]) if row else None


def _select_fulfillment_order_for_update(db: Session, order_id: int) -> Order | None:
    return db.query(Order).filter(Order.id == order_id).with_for_update().first()


def _select_fulfillment_task_for_update(
    db: Session,
    task_id: int,
) -> FulfillmentTask | None:
    return (
        db.query(FulfillmentTask)
        .filter(FulfillmentTask.id == task_id)
        .with_for_update()
        .first()
    )


def lock_fulfillment_task_for_update(
    db: Session,
    task_id: int,
) -> tuple[Order, FulfillmentTask]:
    """Lock a fulfillment transition root as Order -> FulfillmentTask.

    The first read discovers only the task's order id and intentionally does
    not lock the task. The Order row is the canonical root lock. The task is
    locked second and its relationship is revalidated so concurrent/corrupt
    reassignment fails closed instead of reintroducing FulfillmentTask -> Order
    locking.
    """

    expected_order_id = _load_fulfillment_order_id(db, task_id)
    if expected_order_id is None:
        raise HTTPException(status_code=404, detail="Fulfillment task not found")

    order = _select_fulfillment_order_for_update(db, expected_order_id)
    if not order:
        raise HTTPException(
            status_code=409,
            detail="Fulfillment task is linked to a missing order",
        )

    task = _select_fulfillment_task_for_update(db, task_id)
    if not task:
        raise HTTPException(
            status_code=409,
            detail="Fulfillment task changed while being locked",
        )
    if int(task.order_id) != expected_order_id:
        raise HTTPException(
            status_code=409,
            detail="Fulfillment task changed while being locked",
        )
    return order, task
