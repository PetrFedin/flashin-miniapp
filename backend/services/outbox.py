import json
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..models import WebhookDestination, WebhookOutbox


def enqueue_webhook(db: Session, destination: str, event_type: str, payload: dict) -> None:
    db.add(
        WebhookOutbox(
            destination=destination,
            event_type=event_type,
            payload=json.dumps(payload, ensure_ascii=False, default=str),
            status="pending",
            attempts=0,
            next_attempt_at=datetime.utcnow(),
        )
    )


def schedule_retry(row: WebhookOutbox, error: str) -> None:
    row.attempts += 1
    row.last_error = error
    row.status = "failed" if row.attempts >= 10 else "pending"
    row.next_attempt_at = datetime.utcnow() + timedelta(minutes=min(60, 2 ** row.attempts))


def event_subscription_matches(subscription: str, event_type: str) -> bool:
    normalized = (subscription or "*").strip()
    if normalized == "*":
        return True
    if normalized.endswith(".*"):
        return event_type.startswith(normalized[:-1])
    return normalized == event_type


def enqueue_event_for_destinations(db: Session, event_type: str, payload: dict) -> int:
    destinations = (
        db.query(WebhookDestination)
        .filter(WebhookDestination.active.is_(True))
        .order_by(WebhookDestination.id)
        .all()
    )
    queued_urls: set[str] = set()
    for destination in destinations:
        if not event_subscription_matches(destination.event_type, event_type):
            continue
        destination_url = destination.url.strip()
        if not destination_url or destination_url in queued_urls:
            continue
        enqueue_webhook(db, destination_url, event_type, payload)
        queued_urls.add(destination_url)
    return len(queued_urls)
