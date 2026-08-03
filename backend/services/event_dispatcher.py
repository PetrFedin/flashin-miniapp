import json

from sqlalchemy.orm import Session

from ..database import utcnow_naive
from ..models import BusinessEvent
from .outbox import enqueue_event_for_destinations
from .payment_review import ensure_payment_review_case


_DOMAIN_EVENT_HANDLERS = {
    "payment.review_required": ensure_payment_review_case,
}
_MAX_EVENT_ATTEMPTS = 10
_MAX_BATCH_SIZE = 1000


def _apply_domain_handler(db: Session, event_type: str, payload: dict) -> None:
    handler = _DOMAIN_EVENT_HANDLERS.get(event_type)
    if handler:
        handler(db, payload)


def emit_event(
    db: Session,
    event_type: str,
    aggregate_type: str = "",
    aggregate_id: str | int = "",
    payload: dict | None = None,
) -> BusinessEvent:
    normalized_payload = payload or {}
    if not isinstance(normalized_payload, dict):
        raise ValueError("Business event payload must be an object")

    _apply_domain_handler(db, event_type, normalized_payload)
    event = BusinessEvent(
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=str(aggregate_id or ""),
        payload_json=json.dumps(
            normalized_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ),
        status="pending",
    )
    db.add(event)
    return event


def process_event(db: Session, event: BusinessEvent) -> None:
    payload = json.loads(event.payload_json or "{}")
    if not isinstance(payload, dict):
        raise ValueError("Business event payload must be an object")
    enqueue_event_for_destinations(db, event.event_type, payload)
    event.status = "processed"
    event.processed_at = utcnow_naive()


def _validate_batch_limit(limit: int) -> int:
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or limit < 1
        or limit > _MAX_BATCH_SIZE
    ):
        raise ValueError(f"Event processing limit must be between 1 and {_MAX_BATCH_SIZE}")
    return limit


def _claim_pending_events(db: Session, limit: int) -> list[BusinessEvent]:
    return (
        db.query(BusinessEvent)
        .filter(BusinessEvent.status == "pending")
        .order_by(BusinessEvent.id.asc())
        .with_for_update(skip_locked=True)
        .limit(limit)
        .all()
    )


def _record_failed_attempt(event: BusinessEvent) -> None:
    event.attempts = max(int(event.attempts or 0), 0) + 1
    event.status = "failed" if event.attempts >= _MAX_EVENT_ATTEMPTS else "pending"
    event.processed_at = None


def process_pending_events(db: Session, limit: int = 100) -> int:
    """Process one locked batch without duplicate workers or partial outbox rows.

    PostgreSQL ``SKIP LOCKED`` lets multiple workers claim disjoint event rows.
    Each event is isolated in a savepoint so a producer failure rolls back every
    outbox row and state mutation created by that event while preserving the
    retry counter for the failed attempt.
    """
    batch_limit = _validate_batch_limit(limit)
    rows = _claim_pending_events(db, batch_limit)
    processed = 0

    for row in rows:
        try:
            with db.begin_nested():
                process_event(db, row)
            processed += 1
        except Exception:
            _record_failed_attempt(row)

    db.commit()
    return processed
