from __future__ import annotations

import json
import re

from fastapi import HTTPException
from sqlalchemy import CheckConstraint, event

from .models import WebhookDestination, WebhookOutbox
from .services.webhook_security import normalize_webhook_url
from .webhook_statuses import (
    DISCARDED_WEBHOOK_STATUS,
    FAILED_WEBHOOK_STATUS,
    MAX_WEBHOOK_ATTEMPTS,
    MAX_WEBHOOK_BODY_BYTES,
    PENDING_WEBHOOK_STATUS,
    SENT_WEBHOOK_STATUS,
    TERMINAL_WEBHOOK_STATUS_SQL,
    WEBHOOK_OUTBOX_STATUSES,
    WEBHOOK_OUTBOX_STATUS_SQL,
)

_EVENT_TYPE_RE = re.compile(r"^[a-z0-9_.:-]+$")


def _constraint_names(table) -> set[str]:
    return {constraint.name for constraint in table.constraints if constraint.name}


def _check(table, name: str, expression: str) -> None:
    if name not in _constraint_names(table):
        table.append_constraint(CheckConstraint(expression, name=name))


def _normalize_event_type(value: object, *, wildcard: bool) -> str:
    normalized = str(value or ("*" if wildcard else "")).strip().lower()
    if wildcard and normalized == "*":
        return normalized
    if not normalized or len(normalized) > 120 or not _EVENT_TYPE_RE.fullmatch(normalized):
        raise HTTPException(status_code=400, detail="Webhook event type is invalid")
    return normalized


def _canonical_payload(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="Webhook payload is required")
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Webhook payload must be valid JSON") from exc
    if not isinstance(parsed, (dict, list)):
        raise HTTPException(status_code=400, detail="Webhook payload must be a JSON object or array")
    serialized = json.dumps(
        parsed,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(serialized.encode("utf-8")) > MAX_WEBHOOK_BODY_BYTES:
        raise HTTPException(status_code=400, detail="Webhook payload is too large")
    return serialized


def _normalize_destination_before_write(_mapper, _connection, target: WebhookDestination) -> None:
    name = str(target.name or "").strip()
    if not name or len(name) > 255:
        raise HTTPException(status_code=400, detail="Webhook destination name is invalid")
    try:
        url = normalize_webhook_url(str(target.url or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    event_type = _normalize_event_type(target.event_type, wildcard=True)
    secret = str(target.signing_secret or "").strip()
    if secret and not 32 <= len(secret) <= 255:
        raise HTTPException(status_code=400, detail="Webhook signing secret is invalid")

    target.name = name
    target.url = url
    target.event_type = event_type
    target.signing_secret = secret


def _normalize_outbox_before_write(_mapper, _connection, target: WebhookOutbox) -> None:
    status = str(target.status or "").strip().lower()
    if status not in WEBHOOK_OUTBOX_STATUSES:
        raise HTTPException(status_code=400, detail="Webhook outbox status is invalid")
    if isinstance(target.attempts, bool):
        raise HTTPException(status_code=400, detail="Webhook attempt count is invalid")
    try:
        attempts = int(target.attempts)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Webhook attempt count is invalid") from exc
    if attempts < 0 or attempts > MAX_WEBHOOK_ATTEMPTS:
        raise HTTPException(status_code=400, detail="Webhook attempt count is invalid")

    destination = str(target.destination or "").strip()
    if not destination or len(destination) > 255:
        raise HTTPException(status_code=400, detail="Webhook destination is invalid")
    event_type = _normalize_event_type(target.event_type, wildcard=False)
    payload = _canonical_payload(target.payload)
    last_error = str(target.last_error or "").strip()[:2000]

    if status in {PENDING_WEBHOOK_STATUS, SENT_WEBHOOK_STATUS}:
        try:
            destination = normalize_webhook_url(destination)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if status == PENDING_WEBHOOK_STATUS:
        if attempts >= MAX_WEBHOOK_ATTEMPTS or target.next_attempt_at is None:
            raise HTTPException(status_code=400, detail="Pending webhook retry state is invalid")
    elif target.next_attempt_at is not None:
        raise HTTPException(status_code=400, detail="Terminal webhook cannot have a retry schedule")

    if status == FAILED_WEBHOOK_STATUS:
        if attempts != MAX_WEBHOOK_ATTEMPTS or not last_error:
            raise HTTPException(status_code=400, detail="Failed webhook state is invalid")
    if status == DISCARDED_WEBHOOK_STATUS and not last_error:
        raise HTTPException(status_code=400, detail="Discarded webhook requires a reason")

    target.destination = destination
    target.event_type = event_type
    target.payload = payload
    target.status = status
    target.attempts = attempts
    target.last_error = last_error


def apply_webhook_delivery_constraints() -> None:
    _check(
        WebhookDestination.__table__,
        "ck_webhook_destinations_name_nonempty",
        "length(trim(name)) > 0",
    )
    _check(
        WebhookDestination.__table__,
        "ck_webhook_destinations_name_normalized",
        "name = trim(name)",
    )
    _check(
        WebhookDestination.__table__,
        "ck_webhook_destinations_url_normalized",
        "url = trim(url)",
    )
    _check(
        WebhookDestination.__table__,
        "ck_webhook_destinations_event_type_normalized",
        "event_type = lower(trim(event_type))",
    )
    _check(
        WebhookDestination.__table__,
        "ck_webhook_destinations_secret_length",
        "length(signing_secret) = 0 OR length(signing_secret) BETWEEN 32 AND 255",
    )

    _check(
        WebhookOutbox.__table__,
        "ck_webhook_outbox_destination_nonempty",
        "length(trim(destination)) > 0",
    )
    _check(
        WebhookOutbox.__table__,
        "ck_webhook_outbox_event_type_normalized",
        "length(trim(event_type)) > 0 AND event_type = lower(trim(event_type))",
    )
    _check(
        WebhookOutbox.__table__,
        "ck_webhook_outbox_payload_nonempty",
        "length(trim(payload)) > 0",
    )
    _check(
        WebhookOutbox.__table__,
        "ck_webhook_outbox_status_valid",
        f"status IN ({WEBHOOK_OUTBOX_STATUS_SQL})",
    )
    _check(
        WebhookOutbox.__table__,
        "ck_webhook_outbox_attempts_bounded",
        f"attempts BETWEEN 0 AND {MAX_WEBHOOK_ATTEMPTS}",
    )
    _check(
        WebhookOutbox.__table__,
        "ck_webhook_outbox_pending_schedule",
        (
            f"status <> '{PENDING_WEBHOOK_STATUS}' OR "
            f"(next_attempt_at IS NOT NULL AND attempts < {MAX_WEBHOOK_ATTEMPTS})"
        ),
    )
    _check(
        WebhookOutbox.__table__,
        "ck_webhook_outbox_terminal_schedule_empty",
        f"status NOT IN ({TERMINAL_WEBHOOK_STATUS_SQL}) OR next_attempt_at IS NULL",
    )
    _check(
        WebhookOutbox.__table__,
        "ck_webhook_outbox_failed_state",
        (
            f"status <> '{FAILED_WEBHOOK_STATUS}' OR "
            f"(attempts = {MAX_WEBHOOK_ATTEMPTS} AND length(trim(last_error)) > 0)"
        ),
    )
    _check(
        WebhookOutbox.__table__,
        "ck_webhook_outbox_discarded_reason",
        f"status <> '{DISCARDED_WEBHOOK_STATUS}' OR length(trim(last_error)) > 0",
    )

    for model, callback in (
        (WebhookDestination, _normalize_destination_before_write),
        (WebhookOutbox, _normalize_outbox_before_write),
    ):
        for event_name in ("before_insert", "before_update"):
            if not event.contains(model, event_name, callback):
                event.listen(model, event_name, callback)


apply_webhook_delivery_constraints()
