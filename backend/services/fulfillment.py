from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import FulfillmentTask, FulfillmentTaskItem, Order, SlaEvent
from .notifications import queue_order_status

_FULFILLMENT_TRANSITIONS = {
    "new": {"picking", "blocked"},
    "picking": {"packed", "blocked"},
    "blocked": {"picking"},
    "packed": {"ready", "blocked"},
    "ready": set(),
}


def ensure_fulfillment_task(db: Session, order: Order) -> FulfillmentTask:
    task = (
        db.query(FulfillmentTask)
        .filter(FulfillmentTask.order_id == order.id)
        .with_for_update()
        .first()
    )
    if task:
        return task
    task = FulfillmentTask(order_id=order.id, status="new")
    db.add(task)
    db.flush()
    for item in order.items:
        db.add(
            FulfillmentTaskItem(
                task_id=task.id,
                order_item_id=item.id,
                status="to_pick",
                picked_qty=0,
            )
        )
    settings = get_settings()
    db.add(
        SlaEvent(
            order_id=order.id,
            event_type="paid_to_assembling",
            due_at=datetime.utcnow()
            + timedelta(minutes=settings.order_paid_to_assembling_sla_minutes),
            status="open",
        )
    )
    return task


def _resolve_sla(db: Session, order_id: int, event_type: str, now: datetime) -> None:
    events = (
        db.query(SlaEvent)
        .filter(
            SlaEvent.order_id == order_id,
            SlaEvent.event_type == event_type,
            SlaEvent.status == "open",
        )
        .with_for_update()
        .all()
    )
    for event in events:
        event.status = "resolved"
        event.resolved_at = now


def _ensure_assembling_sla(db: Session, order_id: int, now: datetime) -> None:
    existing = (
        db.query(SlaEvent.id)
        .filter(
            SlaEvent.order_id == order_id,
            SlaEvent.event_type == "assembling_to_ready",
            SlaEvent.status == "open",
        )
        .first()
    )
    if existing:
        return
    settings = get_settings()
    db.add(
        SlaEvent(
            order_id=order_id,
            event_type="assembling_to_ready",
            due_at=now
            + timedelta(minutes=settings.order_assembling_to_ready_sla_minutes),
            status="open",
        )
    )


def update_fulfillment_status(
    db: Session,
    task: FulfillmentTask,
    status: str,
    comment: str = "",
) -> None:
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in _FULFILLMENT_TRANSITIONS:
        raise ValueError("Unsupported fulfillment status")
    if normalized_status == task.status:
        if comment:
            task.comment = comment.strip()[:2000]
        return

    allowed = _FULFILLMENT_TRANSITIONS.get(task.status, set())
    if normalized_status not in allowed:
        raise ValueError(f"Fulfillment transition {task.status} -> {normalized_status} is not allowed")
    if normalized_status == "blocked" and len((comment or "").strip()) < 5:
        raise ValueError("Blocked fulfillment task requires a meaningful comment")

    order = (
        db.query(Order)
        .filter(Order.id == task.order_id)
        .with_for_update()
        .first()
    )
    if not order:
        raise ValueError("Fulfillment task is linked to a missing order")
    if order.payment_status not in {"paid", "partially_refunded"}:
        raise ValueError("Only a paid order can enter fulfillment")
    if order.status in {"cancelled", "refunded", "refund_requested"}:
        raise ValueError("Order cannot be fulfilled in its current state")

    now = datetime.utcnow()
    task.status = normalized_status
    if comment:
        task.comment = comment.strip()[:2000]

    if normalized_status == "picking":
        task.pick_started_at = task.pick_started_at or now
        order.status = "assembling"
        order.delivery_status = "assembling"
        _resolve_sla(db, order.id, "paid_to_assembling", now)
        _ensure_assembling_sla(db, order.id, now)
    elif normalized_status == "packed":
        task.packed_at = task.packed_at or now
        order.status = "assembling"
        order.delivery_status = "assembling"
    elif normalized_status == "ready":
        task.ready_at = task.ready_at or now
        order.status = "ready"
        order.delivery_status = "ready"
        _resolve_sla(db, order.id, "assembling_to_ready", now)
    elif normalized_status == "blocked":
        order.status = "assembling"
        order.delivery_status = "assembling"

    queue_order_status(db, order)
