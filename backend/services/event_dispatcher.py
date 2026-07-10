import json
from datetime import datetime
from sqlalchemy.orm import Session
from ..models import BusinessEvent
from .outbox import enqueue_event_for_destinations


def emit_event(db: Session, event_type: str, aggregate_type: str = "", aggregate_id: str | int = "", payload: dict | None = None) -> BusinessEvent:
    event = BusinessEvent(
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=str(aggregate_id or ""),
        payload_json=json.dumps(payload or {}, ensure_ascii=False),
        status="pending",
    )
    db.add(event)
    return event


def process_event(db: Session, event: BusinessEvent) -> None:
    payload = json.loads(event.payload_json or "{}")
    enqueue_event_for_destinations(db, event.event_type, payload)
    event.status = "processed"
    event.processed_at = datetime.utcnow()


def process_pending_events(db: Session, limit: int = 100) -> int:
    rows = db.query(BusinessEvent).filter(BusinessEvent.status == "pending").limit(limit).all()
    count = 0
    for row in rows:
        try:
            process_event(db, row)
            count += 1
        except Exception:
            row.attempts += 1
            row.status = "failed" if row.attempts >= 10 else "pending"
    db.commit()
    return count
