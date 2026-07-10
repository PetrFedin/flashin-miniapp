from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from ..config import get_settings
from ..models import FulfillmentTask, FulfillmentTaskItem, Order, SlaEvent


def ensure_fulfillment_task(db: Session, order: Order) -> FulfillmentTask:
    task = db.query(FulfillmentTask).filter(FulfillmentTask.order_id == order.id).first()
    if task:
        return task
    task = FulfillmentTask(order_id=order.id, status="new")
    db.add(task)
    db.flush()
    for item in order.items:
        db.add(FulfillmentTaskItem(task_id=task.id, order_item_id=item.id, status="to_pick", picked_qty=0))
    settings = get_settings()
    db.add(SlaEvent(
        order_id=order.id,
        event_type="paid_to_assembling",
        due_at=datetime.utcnow() + timedelta(minutes=settings.order_paid_to_assembling_sla_minutes),
        status="open",
    ))
    return task


def update_fulfillment_status(task: FulfillmentTask, status: str, comment: str = "") -> None:
    now = datetime.utcnow()
    task.status = status
    task.comment = comment or task.comment
    if status == "picking":
        task.pick_started_at = task.pick_started_at or now
    elif status == "packed":
        task.packed_at = task.packed_at or now
    elif status == "ready":
        task.ready_at = task.ready_at or now
