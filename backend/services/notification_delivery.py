from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException

from ..models import Notification
from ..notification_models import NotificationDeliveryState
from ..notification_statuses import (
    MAX_NOTIFICATION_ATTEMPTS,
    NOTIFICATION_FAILED,
    NOTIFICATION_PENDING,
    NOTIFICATION_PROCESSING,
    NOTIFICATION_SENT,
    normalize_notification_error,
)

RETRYABLE_NOTIFICATION_STATUSES = {NOTIFICATION_PENDING, NOTIFICATION_FAILED}


@dataclass(frozen=True)
class NotificationErrorDecision:
    retryable: bool
    retry_after_seconds: int | None
    description: str


def _error_description(error: Exception) -> str:
    text = f"{error.__class__.__name__}: {error}"
    return normalize_notification_error(text) or error.__class__.__name__


def classify_notification_error(error: Exception) -> NotificationErrorDecision:
    """Classify delivery failures without importing the Telegram client package.

    aiogram exceptions expose stable class names and, for flood control, a
    ``retry_after`` attribute. Keeping the classifier dependency-free lets the
    backend test the state machine without installing the bot runtime.
    """

    description = _error_description(error)
    retry_after = getattr(error, "retry_after", None)
    if retry_after is not None:
        try:
            retry_after_seconds = max(1, int(float(retry_after)))
        except (TypeError, ValueError, OverflowError):
            retry_after_seconds = None
        return NotificationErrorDecision(True, retry_after_seconds, description)

    class_name = error.__class__.__name__.lower()
    if isinstance(error, (TimeoutError, ConnectionError, OSError)) or any(
        token in class_name
        for token in (
            "networkerror",
            "servererror",
            "timeout",
            "connection",
            "clientconnector",
        )
    ):
        return NotificationErrorDecision(True, None, description)

    if isinstance(error, (ValueError, TypeError)) or any(
        token in class_name
        for token in (
            "badrequest",
            "forbidden",
            "unauthorized",
            "notfound",
            "migratetochat",
        )
    ):
        return NotificationErrorDecision(False, None, description)

    # Unknown exceptions are treated as permanent. Replaying an unclassified
    # programming or provider-contract failure can otherwise spam a recipient.
    return NotificationErrorDecision(False, None, description)


def next_notification_attempt_at(
    attempts: int,
    *,
    now: datetime | None = None,
    retry_after_seconds: int | None = None,
    initial_backoff_seconds: int = 30,
    max_backoff_seconds: int = 3600,
) -> datetime:
    current = now or datetime.utcnow()
    if retry_after_seconds is not None:
        delay = max(1, min(int(retry_after_seconds), max_backoff_seconds))
    else:
        delay = min(
            max(1, initial_backoff_seconds) * (2 ** max(attempts - 1, 0)),
            max(1, max_backoff_seconds),
        )
    return current + timedelta(seconds=delay)


def claim_notification_delivery(
    notification: Notification,
    state: NotificationDeliveryState,
    *,
    now: datetime | None = None,
    lease_seconds: int = 180,
    max_attempts: int = MAX_NOTIFICATION_ATTEMPTS,
) -> str | None:
    """Claim a due notification and fence stale workers with a lease token."""

    current = now or datetime.utcnow()
    bounded_max_attempts = max(1, min(max_attempts, MAX_NOTIFICATION_ATTEMPTS))
    if notification.status not in {NOTIFICATION_PENDING, NOTIFICATION_PROCESSING}:
        return None

    if notification.status == NOTIFICATION_PROCESSING:
        if state.next_attempt_at is None or state.next_attempt_at > current:
            return None
        state.attempts += 1
        state.last_error = "Notification delivery lease expired before completion"
        notification.error = state.last_error
        if state.attempts >= bounded_max_attempts:
            notification.status = NOTIFICATION_FAILED
            state.next_attempt_at = None
            state.lease_token = ""
            state.updated_at = current
            return None

    if state.attempts >= bounded_max_attempts:
        notification.status = NOTIFICATION_FAILED
        notification.error = state.last_error or "Notification retry limit reached"
        state.last_error = notification.error
        state.next_attempt_at = None
        state.lease_token = ""
        state.updated_at = current
        return None

    lease_token = uuid4().hex
    notification.status = NOTIFICATION_PROCESSING
    notification.sent_at = None
    state.lease_token = lease_token
    state.next_attempt_at = current + timedelta(seconds=max(30, lease_seconds))
    state.updated_at = current
    return lease_token


def complete_notification_delivery(
    notification: Notification,
    state: NotificationDeliveryState,
    lease_token: str,
    error: Exception | None = None,
    *,
    now: datetime | None = None,
    max_attempts: int = MAX_NOTIFICATION_ATTEMPTS,
    initial_backoff_seconds: int = 30,
    max_backoff_seconds: int = 3600,
) -> str:
    """Finalize one leased attempt, ignoring stale worker completions."""

    current = now or datetime.utcnow()
    if (
        notification.status != NOTIFICATION_PROCESSING
        or not lease_token
        or state.lease_token != lease_token
    ):
        return "ignored"

    state.lease_token = ""
    state.updated_at = current
    if error is None:
        notification.status = NOTIFICATION_SENT
        notification.sent_at = current
        notification.error = ""
        state.next_attempt_at = None
        if state.attempts == 0:
            state.last_error = ""
        return "sent"

    decision = classify_notification_error(error)
    state.attempts += 1
    state.last_error = decision.description
    notification.error = decision.description
    notification.sent_at = None
    bounded_max_attempts = max(1, min(max_attempts, MAX_NOTIFICATION_ATTEMPTS))

    if not decision.retryable or state.attempts >= bounded_max_attempts:
        notification.status = NOTIFICATION_FAILED
        state.next_attempt_at = None
        return "failed"

    notification.status = NOTIFICATION_PENDING
    state.next_attempt_at = next_notification_attempt_at(
        state.attempts,
        now=current,
        retry_after_seconds=decision.retry_after_seconds,
        initial_backoff_seconds=initial_backoff_seconds,
        max_backoff_seconds=max_backoff_seconds,
    )
    return "retry_scheduled"


def reset_notification_delivery(
    notification: Notification,
    state: NotificationDeliveryState | None = None,
    *,
    now: datetime | None = None,
) -> NotificationDeliveryState:
    """Reset a failed/pending notification for an immediate, audited retry.

    Sent notifications are deliberately not retryable because Telegram does not
    provide an idempotency key for sendMessage and replaying them could create a
    duplicate customer message. Processing rows remain owned by their active
    lease and can only be reclaimed after that lease expires.
    """

    if notification.status == NOTIFICATION_SENT:
        raise HTTPException(status_code=409, detail="Sent notification cannot be retried")
    if notification.status not in RETRYABLE_NOTIFICATION_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Notification in status {notification.status} cannot be retried",
        )

    reset_at = now or datetime.utcnow()
    notification.status = NOTIFICATION_PENDING
    notification.error = ""
    notification.sent_at = None

    if state is None:
        state = NotificationDeliveryState(notification_id=notification.id)
    state.attempts = 0
    state.next_attempt_at = reset_at
    state.last_error = ""
    state.lease_token = ""
    state.updated_at = reset_at
    return state
