import json
from sqlalchemy.orm import Session
from ..models import CustomerTimelineEvent


def add_timeline_event(db: Session, customer_id: int, event_type: str, title: str, payload: dict | None = None) -> None:
    db.add(CustomerTimelineEvent(
        customer_id=customer_id,
        event_type=event_type,
        title=title,
        payload=json.dumps(payload or {}, ensure_ascii=False),
    ))
