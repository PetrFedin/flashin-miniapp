from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from sqlalchemy.orm import Session

from ..database import utcnow_naive
from ..provider_models import ProviderCommand
from .provider_commands import enqueue_provider_command

MEDIA_CLEANUP_PROVIDER = "object_storage"
MEDIA_CLEANUP_COMMAND = "object_storage.media.delete"
MEDIA_CLEANUP_AGGREGATE = "media_cleanup"
_STORAGE_KEY_RE = re.compile(r"^[0-9a-f]{32}\.(?:jpg|png|webp)$")
_ALLOWED_REPLAY_STATES = {"failed", "review_required"}


class MediaCleanupReviewRequired(ValueError):
    pass


def validate_generated_media_storage_key(storage_key: str) -> str:
    key = str(storage_key or "").strip()
    if not _STORAGE_KEY_RE.fullmatch(key):
        raise MediaCleanupReviewRequired("Media cleanup storage key is not a generated media key")
    return key


def media_cleanup_digest(storage_key: str) -> str:
    key = validate_generated_media_storage_key(storage_key)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def media_cleanup_idempotency_key(storage_key: str) -> str:
    return f"media-cleanup:{media_cleanup_digest(storage_key)}"


def enqueue_media_cleanup(
    db: Session,
    storage_key: str,
    *,
    reason: str = "upload_finalize_failed",
) -> ProviderCommand:
    key = validate_generated_media_storage_key(storage_key)
    normalized_reason = str(reason or "upload_finalize_failed").strip().lower()
    if normalized_reason != "upload_finalize_failed":
        raise ValueError("Unsupported media cleanup reason")
    digest = media_cleanup_digest(key)
    command = enqueue_provider_command(
        db,
        provider=MEDIA_CLEANUP_PROVIDER,
        command_type=MEDIA_CLEANUP_COMMAND,
        idempotency_key=media_cleanup_idempotency_key(key),
        aggregate_type=MEDIA_CLEANUP_AGGREGATE,
        aggregate_id=digest,
        payload={"storage_key": key, "reason": normalized_reason},
    )
    db.commit()
    return command


def parse_media_cleanup_command(command: dict[str, Any]) -> str:
    if str(command.get("provider") or "") != MEDIA_CLEANUP_PROVIDER:
        raise MediaCleanupReviewRequired("Unexpected media cleanup provider")
    if str(command.get("command_type") or "") != MEDIA_CLEANUP_COMMAND:
        raise MediaCleanupReviewRequired("Unsupported media cleanup command type")

    try:
        payload = json.loads(str(command.get("payload_json") or ""))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MediaCleanupReviewRequired("Media cleanup payload is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise MediaCleanupReviewRequired("Media cleanup payload is not an object")

    key = validate_generated_media_storage_key(str(payload.get("storage_key") or ""))
    if str(payload.get("reason") or "") != "upload_finalize_failed":
        raise MediaCleanupReviewRequired("Media cleanup reason is invalid")

    digest = media_cleanup_digest(key)
    if str(command.get("aggregate_type") or "") != MEDIA_CLEANUP_AGGREGATE:
        raise MediaCleanupReviewRequired("Media cleanup aggregate type is invalid")
    if str(command.get("aggregate_id") or "") != digest:
        raise MediaCleanupReviewRequired("Media cleanup aggregate binding changed")
    if str(command.get("idempotency_key") or "") != media_cleanup_idempotency_key(key):
        raise MediaCleanupReviewRequired("Media cleanup idempotency binding changed")
    return key


def list_media_cleanup_commands(
    db: Session,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(int(limit), 200))
    rows = (
        db.query(ProviderCommand)
        .filter(
            ProviderCommand.provider == MEDIA_CLEANUP_PROVIDER,
            ProviderCommand.command_type == MEDIA_CLEANUP_COMMAND,
        )
        .order_by(ProviderCommand.id.desc())
        .limit(bounded_limit)
        .all()
    )
    return [
        {
            "id": row.id,
            "status": row.status,
            "attempts": int(row.attempts or 0),
            "object_fingerprint": row.aggregate_id,
            "last_error": row.last_error,
            "next_attempt_at": row.next_attempt_at,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "completed_at": row.completed_at,
        }
        for row in rows
    ]


def requeue_media_cleanup_command(db: Session, command_id: int) -> tuple[ProviderCommand, str]:
    row = (
        db.query(ProviderCommand)
        .filter(ProviderCommand.id == command_id)
        .with_for_update()
        .one_or_none()
    )
    if row is None:
        raise LookupError("Media cleanup command not found")
    if row.provider != MEDIA_CLEANUP_PROVIDER or row.command_type != MEDIA_CLEANUP_COMMAND:
        raise MediaCleanupReviewRequired("Command is not a media cleanup command")
    if row.status not in _ALLOWED_REPLAY_STATES:
        raise ValueError("Only failed or review-required media cleanup can be requeued")

    # Validate the persisted binding before changing status. There is no API
    # parameter for storage_key, so operators cannot redirect deletion.
    parse_media_cleanup_command(
        {
            "provider": row.provider,
            "command_type": row.command_type,
            "idempotency_key": row.idempotency_key,
            "aggregate_type": row.aggregate_type,
            "aggregate_id": row.aggregate_id,
            "payload_json": row.payload_json,
        }
    )
    previous_status = row.status
    row.status = "pending"
    row.next_attempt_at = utcnow_naive()
    row.lease_token = None
    row.completed_at = None
    db.flush()
    return row, previous_status
