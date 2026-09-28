import json
import re
from datetime import timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from ..database import utcnow_naive
from ..models import WebhookDestination, WebhookOutbox
from .webhook_security import is_internal_destination, normalize_webhook_url

_MAX_OUTBOX_ATTEMPTS = 10
_MAX_WEBHOOK_BODY_BYTES = 256 * 1024
_EVENT_TYPE_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


def _json_default(value):
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise TypeError("Decimal payload values must be finite")
        # Preserve the existing JSON-number contract at the webhook boundary.
        return float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _serialize_payload(payload: dict | list) -> str:
    if not isinstance(payload, (dict, list)):
        raise ValueError("Webhook payload must be a JSON object or array")
    try:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
            default=_json_default,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Webhook payload is not valid JSON") from exc
    if len(serialized.encode("utf-8")) > _MAX_WEBHOOK_BODY_BYTES:
        raise ValueError("Webhook payload is too large")
    return serialized


def _normalize_event_type(event_type: str) -> str:
    normalized = (event_type or "").strip()
    if not normalized or len(normalized) > 120 or not _EVENT_TYPE_RE.fullmatch(normalized):
        raise ValueError("Webhook event type is invalid")
    return normalized


def enqueue_webhook(
    db: Session,
    destination: str,
    event_type: str,
    payload: dict | list,
) -> bool:
    raw_destination = (destination or "").strip()
    if is_internal_destination(raw_destination):
        return False

    normalized_event_type = _normalize_event_type(event_type)
    serialized_payload = _serialize_payload(payload)

    try:
        normalized_destination = normalize_webhook_url(raw_destination)
    except ValueError as exc:
        db.add(
            WebhookOutbox(
                destination=raw_destination[:255],
                event_type=normalized_event_type,
                payload=serialized_payload,
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
            payload=serialized_payload,
            status="pending",
            attempts=0,
            next_attempt_at=utcnow_naive(),
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
    row.next_attempt_at = utcnow_naive() + timedelta(
        minutes=min(60, 2 ** row.attempts)
    )


def enqueue_event_for_destinations(db: Session, event_type: str, payload: dict | list) -> int:
    normalized_event_type = _normalize_event_type(event_type)
    destinations = (
        db.query(WebhookDestination)
        .filter(WebhookDestination.active.is_(True))
        .order_by(WebhookDestination.id.asc())
        .all()
    )
    count = 0
    for destination in destinations:
        if destination.event_type not in {"*", normalized_event_type}:
            continue
        if enqueue_webhook(
            db,
            destination.url,
            normalized_event_type,
            payload,
        ):
            count += 1
    return count
