from fastapi import HTTPException

NOTIFICATION_PENDING = "pending"
NOTIFICATION_PROCESSING = "processing"
NOTIFICATION_SENT = "sent"
NOTIFICATION_FAILED = "failed"
NOTIFICATION_DISCARDED = "discarded"

VALID_NOTIFICATION_STATUSES = frozenset(
    {
        NOTIFICATION_PENDING,
        NOTIFICATION_PROCESSING,
        NOTIFICATION_SENT,
        NOTIFICATION_FAILED,
        NOTIFICATION_DISCARDED,
    }
)
VALID_NOTIFICATION_STATUS_SQL = ", ".join(
    f"'{status}'" for status in sorted(VALID_NOTIFICATION_STATUSES)
)

MAX_NOTIFICATION_ATTEMPTS = 10
MAX_NOTIFICATION_MESSAGE_LENGTH = 4096
MAX_NOTIFICATION_ERROR_LENGTH = 2000
MAX_NOTIFICATION_TELEGRAM_ID_LENGTH = 64
MAX_NOTIFICATION_DEDUPLICATION_KEY_LENGTH = 255
MAX_NOTIFICATION_LEASE_TOKEN_LENGTH = 64


def normalize_notification_status(value: object) -> str:
    return str(value or "").strip().lower()


def normalize_telegram_id(value: object) -> str:
    normalized = str(value or "").strip()
    if not normalized or normalized.startswith("deleted:"):
        raise HTTPException(status_code=400, detail="Telegram chat id is required")
    if len(normalized) > MAX_NOTIFICATION_TELEGRAM_ID_LENGTH:
        raise HTTPException(status_code=400, detail="Telegram chat id is too long")
    try:
        numeric_id = int(normalized)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Telegram chat id must be numeric") from exc
    if numeric_id == 0:
        raise HTTPException(status_code=400, detail="Telegram chat id cannot be zero")
    return str(numeric_id)


def normalize_notification_message(value: object, *, truncate: bool) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="Notification message is required")
    if len(normalized) > MAX_NOTIFICATION_MESSAGE_LENGTH:
        if not truncate:
            raise HTTPException(status_code=400, detail="Notification message is too long")
        normalized = normalized[: MAX_NOTIFICATION_MESSAGE_LENGTH - 1].rstrip() + "…"
    return normalized


def normalize_notification_error(value: object) -> str:
    normalized = str(value or "").strip()
    if len(normalized) > MAX_NOTIFICATION_ERROR_LENGTH:
        normalized = normalized[:MAX_NOTIFICATION_ERROR_LENGTH].rstrip()
    return normalized


def normalize_notification_deduplication_key(value: object) -> str:
    normalized = str(value or "").strip()
    if len(normalized) > MAX_NOTIFICATION_DEDUPLICATION_KEY_LENGTH:
        raise HTTPException(
            status_code=400,
            detail="Notification deduplication key is too long",
        )
    return normalized
