from datetime import datetime
from sqlalchemy.orm import Session
from ..models import Notification, SlaEvent


def mark_overdue_sla(db: Session) -> int:
    rows = (
        db.query(SlaEvent)
        .filter(SlaEvent.status == "open", SlaEvent.due_at < datetime.utcnow())
        .all()
    )
    count = 0
    for row in rows:
        row.status = "overdue"
        db.add(Notification(
            telegram_id="admin",
            message=f"⚠️ SLA overdue: order #{row.order_id}, {row.event_type}",
            status="pending",
        ))
        count += 1
    db.commit()
    return count
