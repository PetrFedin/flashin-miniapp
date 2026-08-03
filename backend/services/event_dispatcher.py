import json
from datetime import datetime

from sqlalchemy.orm import Session

from ..business_event_models import BusinessEventRecoveryState
from ..database import utcnow_naive
from ..models import BusinessEvent
from .outbox import enqueue_event_for_destinations
from .payment_review import ensure_payment_review_case


_DOMAIN_EVENT_HANDLERS = {
    "payment.review_required": ensure_payment_review_case,
}
_MAX_EVENT_ATTEMPTS = 10
_MAX_BATCH_SIZE = 1000
_MAX_ERROR_LENGTH = 4000


class BusinessEventNotFoundError(LookupError):
    pass


class BusinessEventReplayConflictError(RuntimeError):
    pass


class BusinessEventPayloadError(ValueError):
    pass


def _serialize_payload(payload: dict) -> str:
    if not isinstance(payload, dict):
        raise BusinessEventPayloadError("Business event payload must be an object")
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise BusinessEventPayloadError("Business event payload is not JSON serializable") from exc


def _parse_payload(payload_json: str) -> dict:
    try:
        payload = json.loads(payload_json or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise BusinessEventPayloadError("Stored business event payload is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise BusinessEventPayloadError("Business event payload must be an object")
    _serialize_payload(payload)
    return payload


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
    normalized_payload = {} if payload is None else payload
    serialized_payload = _serialize_payload(normalized_payload)
    _apply_domain_handler(db, event_type, normalized_payload)
    event = BusinessEvent(
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=str(aggregate_id or ""),
        payload_json=serialized_payload,
        status="pending",
    )
    db.add(event)
    return event


def process_event(db: Session, event: BusinessEvent) -> None:
    payload = _parse_payload(event.payload_json)
    enqueue_event_for_destinations(db, event.event_type, payload)
    processed_at = utcnow_naive()
    event.status = "processed"
    event.processed_at = processed_at

    recovery = db.get(BusinessEventRecoveryState, event.id)
    if recovery:
        recovery.resolved_at = processed_at


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


def _format_failure(error: Exception) -> str:
    message = str(error).strip()
    rendered = error.__class__.__name__ if not message else f"{error.__class__.__name__}: {message}"
    return rendered[:_MAX_ERROR_LENGTH]


def _get_or_create_recovery_state(
    db: Session,
    event_id: int,
) -> BusinessEventRecoveryState:
    recovery = db.get(BusinessEventRecoveryState, event_id)
    if recovery is None:
        recovery = BusinessEventRecoveryState(business_event_id=event_id)
        db.add(recovery)
    return recovery


def _record_failed_attempt(
    db: Session,
    event: BusinessEvent,
    error: Exception,
) -> None:
    attempted_at = utcnow_naive()
    event.attempts = max(int(event.attempts or 0), 0) + 1
    event.status = "failed" if event.attempts >= _MAX_EVENT_ATTEMPTS else "pending"
    event.processed_at = None

    recovery = _get_or_create_recovery_state(db, event.id)
    recovery.last_error = _format_failure(error)
    recovery.last_attempt_at = attempted_at
    recovery.resolved_at = None
    if event.status == "failed":
        recovery.failed_at = attempted_at


def requeue_failed_event(
    db: Session,
    event_id: int,
    *,
    replacement_payload: dict | None = None,
    admin_id: int | None = None,
    replayed_at: datetime | None = None,
) -> tuple[BusinessEvent, BusinessEventRecoveryState, dict]:
    """Atomically return one terminal event to the worker queue.

    The row lock makes concurrent replay requests mutually exclusive. Only a
    terminal ``failed`` event can be requeued; the endpoint never executes the
    external side effect synchronously.
    """
    event = (
        db.query(BusinessEvent)
        .filter(BusinessEvent.id == event_id)
        .with_for_update()
        .first()
    )
    if event is None:
        raise BusinessEventNotFoundError("Business event not found")
    if event.status != "failed":
        raise BusinessEventReplayConflictError(
            f"Only failed events can be replayed; current status is {event.status}"
        )

    payload = (
        replacement_payload
        if replacement_payload is not None
        else _parse_payload(event.payload_json)
    )
    serialized_payload = _serialize_payload(payload)
    replay_time = replayed_at or utcnow_naive()

    recovery = (
        db.query(BusinessEventRecoveryState)
        .filter(BusinessEventRecoveryState.business_event_id == event.id)
        .with_for_update()
        .first()
    )
    if recovery is None:
        recovery = BusinessEventRecoveryState(business_event_id=event.id)
        db.add(recovery)

    before = {
        "status": event.status,
        "attempts": int(event.attempts or 0),
        "last_error": recovery.last_error,
        "failed_at": recovery.failed_at.isoformat() if recovery.failed_at else None,
        "replay_count": int(recovery.replay_count or 0),
        "payload_replaced": replacement_payload is not None,
    }

    event.payload_json = serialized_payload
    event.status = "pending"
    event.attempts = 0
    event.processed_at = None
    recovery.replay_count = max(int(recovery.replay_count or 0), 0) + 1
    recovery.last_replayed_at = replay_time
    recovery.last_replayed_by_admin_id = admin_id
    recovery.failed_at = None
    recovery.resolved_at = None
    return event, recovery, before


def process_pending_events(db: Session, limit: int = 100) -> int:
    """Process one locked batch without duplicate workers or partial outbox rows.

    PostgreSQL ``SKIP LOCKED`` lets multiple workers claim disjoint event rows.
    Each event is isolated in a savepoint so a producer failure rolls back every
    outbox row and state mutation created by that event while preserving the
    retry counter and durable diagnostics for the failed attempt.
    """
    batch_limit = _validate_batch_limit(limit)
    rows = _claim_pending_events(db, batch_limit)
    processed = 0

    for row in rows:
        try:
            with db.begin_nested():
                process_event(db, row)
            processed += 1
        except Exception as exc:
            _record_failed_attempt(db, row, exc)

    db.commit()
    return processed
