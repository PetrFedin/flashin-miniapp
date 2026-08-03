import json

from sqlalchemy.orm import Session

from ..database import utcnow_naive
from ..models import BusinessEvent
from .outbox import enqueue_event_for_destinations
from .payment_review import ensure_payment_review_case


_DOMAIN_EVENT_HANDLERS = {
    "payment.review_required": ensure_payment_review_case,
}


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


def process_pending_events(db: Session, limit: int = 100) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 1000:
        raise ValueError("Event processing limit must be between 1 and 1000")

    rows = (
        db.query(BusinessEvent)
        .filter(BusinessEvent.status == "pending")
        .order_by(BusinessEvent.id.asc())
        .limit(limit)
        .all()
    )
    count = 0
    for row in rows:
        try:
            process_event(db, row)
            count += 1
        except Exception:
            row.attempts = max(int(row.attempts or 0), 0) + 1
            row.status = "failed" if row.attempts >= 10 else "pending"
    db.commit()
    return count
