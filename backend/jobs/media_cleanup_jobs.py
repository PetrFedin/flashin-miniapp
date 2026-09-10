from __future__ import annotations

from sqlalchemy.orm import Session

from ..services.media_cleanup import (
    MEDIA_CLEANUP_PROVIDER,
    MediaCleanupReviewRequired,
    parse_media_cleanup_command,
)
from ..services.media_storage import delete_media
from ..services.provider_commands import (
    claim_provider_commands,
    fail_provider_command,
    finish_provider_command,
)

_PERMANENT_STORAGE_CODES = {
    "AccessDenied",
    "AccountProblem",
    "AllAccessDisabled",
    "InvalidAccessKeyId",
    "InvalidBucketName",
    "NoSuchBucket",
    "SignatureDoesNotMatch",
}


def _requires_operator_review(exc: Exception) -> bool:
    try:
        from botocore.exceptions import ClientError
    except ImportError:
        return False
    if not isinstance(exc, ClientError):
        return False
    response = getattr(exc, "response", {}) or {}
    error = response.get("Error", {}) if isinstance(response, dict) else {}
    code = str(error.get("Code") or "") if isinstance(error, dict) else ""
    return code in _PERMANENT_STORAGE_CODES


def process_media_cleanup_commands(db: Session, limit: int = 50) -> dict[str, int]:
    claimed = claim_provider_commands(db, provider=MEDIA_CLEANUP_PROVIDER, limit=limit)
    result = {
        "claimed": len(claimed),
        "deleted": 0,
        "retry_scheduled": 0,
        "failed": 0,
        "review_required": 0,
        "ignored": 0,
    }

    for command in claimed:
        command_id = int(command["id"])
        lease_token = str(command["lease_token"])
        try:
            storage_key = parse_media_cleanup_command(command)
            if db.in_transaction():
                raise RuntimeError("database transaction must be closed before media cleanup I/O")
            delete_media(storage_key)
            if finish_provider_command(
                db,
                command_id,
                lease_token,
                external_id=f"deleted:{command['aggregate_id']}",
            ):
                result["deleted"] += 1
            else:
                result["ignored"] += 1
        except MediaCleanupReviewRequired as exc:
            state = fail_provider_command(
                db,
                command_id,
                lease_token,
                exc,
                review_required=True,
            )
            result[state] = result.get(state, 0) + 1
        except Exception as exc:
            state = fail_provider_command(
                db,
                command_id,
                lease_token,
                exc,
                review_required=_requires_operator_review(exc),
            )
            result[state] = result.get(state, 0) + 1

    return result
