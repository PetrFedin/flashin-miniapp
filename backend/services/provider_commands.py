from __future__ import annotations

import json
import re
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..database import utcnow_naive
from ..provider_models import ProviderCommand

_PROVIDER_RE = re.compile(r"^[a-z0-9_.-]{2,64}$")
_COMMAND_RE = re.compile(r"^[a-z0-9_.:-]{3,120}$")
_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,255}$")
_MAX_ATTEMPTS = 10
_LEASE_MINUTES = 5
_MAX_BATCH = 100
_MAX_ERROR_LENGTH = 4000


class ProviderCommandConflictError(ValueError):
    pass


def _canonical_payload(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        raise ValueError("Provider command payload must be an object")
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Provider command payload must be JSON serializable") from exc


def _normalize_provider(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _PROVIDER_RE.fullmatch(normalized):
        raise ValueError("Provider name is invalid")
    return normalized


def _normalize_command_type(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _COMMAND_RE.fullmatch(normalized):
        raise ValueError("Provider command type is invalid")
    return normalized


def _normalize_idempotency_key(value: str) -> str:
    normalized = str(value or "").strip()
    if not _IDEMPOTENCY_RE.fullmatch(normalized):
        raise ValueError("Provider command idempotency key is invalid")
    return normalized


def enqueue_provider_command(
    db: Session,
    *,
    provider: str,
    command_type: str,
    idempotency_key: str,
    aggregate_type: str = "",
    aggregate_id: str | int = "",
    payload: dict[str, Any] | None = None,
) -> ProviderCommand:
    normalized_provider = _normalize_provider(provider)
    normalized_type = _normalize_command_type(command_type)
    normalized_key = _normalize_idempotency_key(idempotency_key)
    normalized_aggregate_type = str(aggregate_type or "").strip().lower()[:64]
    normalized_aggregate_id = str(aggregate_id or "").strip()[:128]
    payload_json = _canonical_payload(payload or {})

    for candidate in getattr(db, "new", ()):
        if (
            isinstance(candidate, ProviderCommand)
            and candidate.provider == normalized_provider
            and candidate.idempotency_key == normalized_key
        ):
            if (
                candidate.command_type != normalized_type
                or candidate.aggregate_type != normalized_aggregate_type
                or candidate.aggregate_id != normalized_aggregate_id
                or candidate.payload_json != payload_json
            ):
                raise ProviderCommandConflictError(
                    "Provider command idempotency key was reused with different data"
                )
            return candidate

    existing = (
        db.query(ProviderCommand)
        .filter(
            ProviderCommand.provider == normalized_provider,
            ProviderCommand.idempotency_key == normalized_key,
        )
        .first()
    )
    if existing:
        if (
            existing.command_type != normalized_type
            or existing.aggregate_type != normalized_aggregate_type
            or existing.aggregate_id != normalized_aggregate_id
            or existing.payload_json != payload_json
        ):
            raise ProviderCommandConflictError(
                "Provider command idempotency key was reused with different data"
            )
        return existing

    command = ProviderCommand(
        provider=normalized_provider,
        command_type=normalized_type,
        idempotency_key=normalized_key,
        aggregate_type=normalized_aggregate_type,
        aggregate_id=normalized_aggregate_id,
        payload_json=payload_json,
        status="pending",
    )
    db.add(command)
    return command


def claim_provider_commands(
    db: Session,
    *,
    provider: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    normalized_provider = _normalize_provider(provider)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_BATCH:
        raise ValueError(f"Provider command batch size must be between 1 and {_MAX_BATCH}")

    now = utcnow_naive()
    rows = (
        db.query(ProviderCommand)
        .filter(
            ProviderCommand.provider == normalized_provider,
            or_(
                (
                    (ProviderCommand.status == "pending")
                    & or_(
                        ProviderCommand.next_attempt_at.is_(None),
                        ProviderCommand.next_attempt_at <= now,
                    )
                ),
                (
                    (ProviderCommand.status == "processing")
                    & or_(
                        ProviderCommand.next_attempt_at.is_(None),
                        ProviderCommand.next_attempt_at <= now,
                    )
                ),
            ),
        )
        .order_by(ProviderCommand.id.asc())
        .with_for_update(skip_locked=True)
        .limit(limit)
        .all()
    )

    claimed: list[dict[str, Any]] = []
    for row in rows:
        lease_token = uuid.uuid4().hex
        row.status = "processing"
        row.lease_token = lease_token
        row.next_attempt_at = now + timedelta(minutes=_LEASE_MINUTES)
        claimed.append(
            {
                "id": row.id,
                "provider": row.provider,
                "command_type": row.command_type,
                "idempotency_key": row.idempotency_key,
                "aggregate_type": row.aggregate_type,
                "aggregate_id": row.aggregate_id,
                "payload_json": row.payload_json,
                "lease_token": lease_token,
            }
        )
    db.commit()
    return claimed


def renew_provider_command_lease(db: Session, command_id: int, lease_token: str) -> bool:
    token = str(lease_token or "").strip()
    if not token:
        return False
    row = (
        db.query(ProviderCommand)
        .filter(
            ProviderCommand.id == command_id,
            ProviderCommand.status == "processing",
            ProviderCommand.lease_token == token,
        )
        .with_for_update()
        .first()
    )
    if not row:
        db.rollback()
        return False
    row.next_attempt_at = utcnow_naive() + timedelta(minutes=_LEASE_MINUTES)
    db.commit()
    return True


def finish_provider_command(
    db: Session,
    command_id: int,
    lease_token: str,
    *,
    external_id: str,
) -> bool:
    token = str(lease_token or "").strip()
    normalized_external_id = str(external_id or "").strip()[:255]
    if not token or not normalized_external_id:
        return False
    row = (
        db.query(ProviderCommand)
        .filter(
            ProviderCommand.id == command_id,
            ProviderCommand.status == "processing",
            ProviderCommand.lease_token == token,
        )
        .with_for_update()
        .first()
    )
    if not row:
        db.rollback()
        return False
    row.status = "sent"
    row.external_id = normalized_external_id
    row.last_error = ""
    row.next_attempt_at = None
    row.lease_token = None
    row.completed_at = utcnow_naive()
    db.commit()
    return True


def fail_provider_command(
    db: Session,
    command_id: int,
    lease_token: str,
    error: Exception | str,
    *,
    review_required: bool = False,
) -> str:
    token = str(lease_token or "").strip()
    if not token:
        return "ignored"
    row = (
        db.query(ProviderCommand)
        .filter(
            ProviderCommand.id == command_id,
            ProviderCommand.status == "processing",
            ProviderCommand.lease_token == token,
        )
        .with_for_update()
        .first()
    )
    if not row:
        db.rollback()
        return "ignored"

    rendered_error = str(error).strip() or error.__class__.__name__ if isinstance(error, Exception) else str(error)
    row.attempts = max(int(row.attempts or 0), 0) + 1
    row.last_error = rendered_error[:_MAX_ERROR_LENGTH]
    row.lease_token = None
    row.completed_at = None

    if review_required:
        row.status = "review_required"
        row.next_attempt_at = None
        db.commit()
        return "review_required"

    if row.attempts >= _MAX_ATTEMPTS:
        row.status = "failed"
        row.next_attempt_at = None
        db.commit()
        return "failed"

    row.status = "pending"
    row.next_attempt_at = utcnow_naive() + timedelta(minutes=min(60, 2 ** row.attempts))
    db.commit()
    return "retry_scheduled"
