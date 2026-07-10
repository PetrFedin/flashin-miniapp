import json
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from ..models import WebhookDestination, WebhookOutbox


def enqueue_webhook(db: Session, destination: str, event_type: str, payload: dict) -> None:
    db.add(WebhookOutbox(
        destination=destination,
        event_type=event_type,
        payload=json.dumps(payload, ensure_ascii=False),
        status="pending",
        attempts=0,
        next_attempt_at=datetime.utcnow(),
    ))


def schedule_retry(row: WebhookOutbox, error: str) -> None:
    row.attempts += 1
    row.last_error = error
    row.status = "failed" if row.attempts >= 10 else "pending"
    row.next_attempt_at = datetime.utcnow() + timedelta(minutes=min(60, 2 ** row.attempts))



def enqueue_event_for_destinations(db: Session, event_type: str, payload: dict) -> int:
    destinations = db.query(WebhookDestination).filter(WebhookDestination.active == True).all()
    count = 0
    for dest in destinations:
        if dest.event_type not in {"*", event_type}:
            continue
        enqueue_webhook(db, dest.url, event_type, payload)
        count += 1
    return count
