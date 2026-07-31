import json
import uuid
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..models import BusinessEvent
from ..queue_integrity import EVENT_MAX_ATTEMPTS, QUEUE_ERROR_MAX_LENGTH
from .outbox import enqueue_event_for_destinations

_EVENT_LEASE = timedelta(minutes=5)


def _bounded_error(exc: object, fallback: str = "Business event dispatch failed") -> str:
    value = str(exc or fallback).strip() or fallback
    return value[:QUEUE_ERROR_MAX_LENGTH]


def _retry_at(attempts: int, now: datetime) -> datetime:
    return now + timedelta(minutes=min(60, 2 ** max(1, attempts)))


def emit_event(
    db: Session,
    event_type: str,
    aggregate_type: str = "",
    aggregate_id: str | int = "",
    payload: dict | None = None,
) -> BusinessEvent:
    serialized = json.dumps(
        payload or {},
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )
    event = BusinessEvent(
        event_type=str(event_type or "").strip(),
        aggregate_type=str(aggregate_type or "").strip(),
        aggregate_id=str(aggregate_id or "").strip(),
        payload_json=serialized,
        status="pending",
        attempts=0,
        next_attempt_at=datetime.utcnow(),
        lease_token="",
        lease_expires_at=None,
        last_error="",
        processed_at=None,
    )
    db.add(event)
    return event


def _load_payload(event: BusinessEvent) -> dict | list:
    payload = json.loads(event.payload_json or "{}")
    if not isinstance(payload, (dict, list)):
        raise ValueError("Business event payload must be a JSON object or array")
    return payload


def process_event(db: Session, event: BusinessEvent) -> None:
    payload = _load_payload(event)
    enqueue_event_for_destinations(db, event.event_type, payload)
    event.status = "processed"
    event.processed_at = datetime.utcnow()
    event.next_attempt_at = None
    event.lease_token = ""
    event.lease_expires_at = None
    event.last_error = ""


def _schedule_failure(
    event: BusinessEvent,
    exc: Exception,
    *,
    now: datetime,
    permanent: bool,
) -> None:
    attempts = EVENT_MAX_ATTEMPTS if permanent else min(
        int(event.attempts or 0) + 1,
        EVENT_MAX_ATTEMPTS,
    )
    event.attempts = attempts
    event.last_error = _bounded_error(exc)
    event.processed_at = None
    event.lease_token = ""
    event.lease_expires_at = None
    if attempts >= EVENT_MAX_ATTEMPTS:
        event.status = "failed"
        event.next_attempt_at = None
    else:
        event.status = "pending"
        event.next_attempt_at = _retry_at(attempts, now)


def _recover_expired_leases(db: Session, now: datetime) -> int:
    rows = (
        db.query(BusinessEvent)
        .filter(
            BusinessEvent.status == "processing",
            BusinessEvent.lease_expires_at.is_not(None),
            BusinessEvent.lease_expires_at <= now,
        )
        .order_by(BusinessEvent.id.asc())
        .with_for_update(skip_locked=True)
        .all()
    )
    for row in rows:
        _schedule_failure(
            row,
            RuntimeError("Business event processing lease expired"),
            now=now,
            permanent=False,
        )
    return len(rows)


def _claim_pending_events(
    db: Session,
    *,
    limit: int,
    now: datetime,
) -> list[tuple[int, str]]:
    _recover_expired_leases(db, now)
    rows = (
        db.query(BusinessEvent)
        .filter(
            BusinessEvent.status == "pending",
            BusinessEvent.next_attempt_at.is_not(None),
            BusinessEvent.next_attempt_at <= now,
        )
        .order_by(BusinessEvent.id.asc())
        .with_for_update(skip_locked=True)
        .limit(limit)
        .all()
    )
    claims: list[tuple[int, str]] = []
    for row in rows:
        token = uuid.uuid4().hex
        row.status = "processing"
        row.next_attempt_at = None
        row.lease_token = token
        row.lease_expires_at = now + _EVENT_LEASE
        row.processed_at = None
        claims.append((row.id, token))
    db.commit()
    return claims


def _is_permanent_error(exc: Exception) -> bool:
    return isinstance(
        exc,
        (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError),
    )


def _process_claim(db: Session, event_id: int, lease_token: str) -> bool:
    event = (
        db.query(BusinessEvent)
        .filter(
            BusinessEvent.id == event_id,
            BusinessEvent.status == "processing",
            BusinessEvent.lease_token == lease_token,
        )
        .with_for_update()
        .first()
    )
    if not event:
        db.rollback()
        return False

    try:
        with db.begin_nested():
            process_event(db, event)
            db.flush()
        db.commit()
        return True
    except Exception as exc:
        current = (
            db.query(BusinessEvent)
            .filter(
                BusinessEvent.id == event_id,
                BusinessEvent.status == "processing",
                BusinessEvent.lease_token == lease_token,
            )
            .with_for_update()
            .first()
        )
        if current is None:
            db.rollback()
            return False
        _schedule_failure(
            current,
            exc,
            now=datetime.utcnow(),
            permanent=_is_permanent_error(exc),
        )
        db.commit()
        return False


def process_pending_events(db: Session, limit: int = 100) -> int:
    try:
        normalized_limit = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValueError("Event processing limit must be an integer") from exc
    if normalized_limit < 1 or normalized_limit > 1_000:
        raise ValueError("Event processing limit is out of range")

    claims = _claim_pending_events(
        db,
        limit=normalized_limit,
        now=datetime.utcnow(),
    )
    processed = 0
    for event_id, lease_token in claims:
        if _process_claim(db, event_id, lease_token):
            processed += 1
    return processed
