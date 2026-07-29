import json
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..models import WebhookDestination, WebhookOutbox
from .webhook_security import is_internal_destination, normalize_webhook_url


_MAX_OUTBOX_ATTEMPTS = 10


def enqueue_webhook(db: Session, destination: str, event_type: str, payload: dict) -> bool:
    raw_destination = (destination or "").strip()
    if is_internal_destination(raw_destination):
        return False

    normalized_event_type = (event_type or "").strip()[:120]
    if not normalized_event_type:
        raise ValueError("Webhook event type is required")

    try:
        normalized_destination = normalize_webhook_url(raw_destination)
    except ValueError as exc:
        db.add(
            WebhookOutbox(
                destination=raw_destination[:255],
                event_type=normalized_event_type,
                payload=json.dumps(payload, ensure_ascii=False),
                status="failed",
                attempts=_MAX_OUTBOX_ATTEMPTS,
                last_error=str(exc)[:2000],
                next_attempt_at=None,
            )
        )
        return False

    db.add(
        WebhookOutbox(
            destination=normalized_destination,
            event_type=normalized_event_type,
            payload=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            status="pending",
            attempts=0,
            next_attempt_at=datetime.utcnow(),
        )
    )
    return True


def schedule_retry(row: WebhookOutbox, error: str) -> None:
    row.attempts = max(int(row.attempts or 0), 0) + 1
    row.last_error = (error or "Webhook delivery failed")[:2000]
    if row.attempts >= _MAX_OUTBOX_ATTEMPTS:
        row.status = "failed"
        row.next_attempt_at = None
        return
    row.status = "pending"
    row.next_attempt_at = datetime.utcnow() + timedelta(minutes=min(60, 2 ** row.attempts))


def enqueue_event_for_destinations(db: Session, event_type: str, payload: dict) -> int:
    destinations = (
        db.query(WebhookDestination)
        .filter(WebhookDestination.active.is_(True))
        .order_by(WebhookDestination.id.asc())
        .all()
    )
    count = 0
    for destination in destinations:
        if destination.event_type not in {"*", event_type}:
            continue
        if enqueue_webhook(db, destination.url, event_type, payload):
            count += 1
    return count
