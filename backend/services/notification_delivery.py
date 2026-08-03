import os
import uuid
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..database import utcnow_naive
from ..models import Notification
from ..notification_models import NotificationDeliveryState


RETRYABLE_NOTIFICATION_STATUSES = {"pending", "failed"}
BATCH_SIZE = max(1, min(int(os.getenv("NOTIFICATION_BATCH_SIZE", "50")), 200))
MAX_ATTEMPTS = max(1, min(int(os.getenv("NOTIFICATION_MAX_ATTEMPTS", "5")), 20))
INITIAL_BACKOFF_SECONDS = max(
    5,
    int(os.getenv("NOTIFICATION_INITIAL_BACKOFF_SECONDS", "30")),
)
MAX_BACKOFF_SECONDS = max(
    INITIAL_BACKOFF_SECONDS,
    int(os.getenv("NOTIFICATION_MAX_BACKOFF_SECONDS", "3600")),
)
LEASE_SECONDS = max(30, int(os.getenv("NOTIFICATION_LEASE_SECONDS", "180")))


def reset_notification_delivery(
    notification: Notification,
    state: NotificationDeliveryState | None = None,
    *,
    now: datetime | None = None,
) -> NotificationDeliveryState:
    """Reset a failed/pending notification for an immediate, audited retry.

    Sent notifications are deliberately not retryable because Telegram does not
    provide an idempotency key for sendMessage and replaying them could create a
    duplicate customer message.
    """
    if notification.status == "sent":
        raise HTTPException(status_code=409, detail="Sent notification cannot be retried")
    if notification.status not in RETRYABLE_NOTIFICATION_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Notification in status {notification.status} cannot be retried",
        )

    reset_at = now or utcnow_naive()
    notification.status = "pending"
    notification.error = ""
    notification.sent_at = None

    if state is None:
        state = NotificationDeliveryState(notification_id=notification.id)
    state.attempts = 0
    state.next_attempt_at = reset_at
    state.lease_token = None
    state.last_error = ""
    state.updated_at = reset_at
    return state


def validate_batch_size(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError("Notification batch size must be an integer")
    if limit < 1 or limit > 200:
        raise ValueError("Notification batch size must be between 1 and 200")
    return limit


def next_attempt_at(attempts: int, *, now: datetime | None = None) -> datetime:
    delay = min(
        INITIAL_BACKOFF_SECONDS * (2 ** max(attempts - 1, 0)),
        MAX_BACKOFF_SECONDS,
    )
    return (now or utcnow_naive()) + timedelta(seconds=delay)


def claim_pending_batch(
    db: Session,
    limit: int = BATCH_SIZE,
) -> list[dict]:
    batch_size = validate_batch_size(limit)
    now = utcnow_naive()
    rows = (
        db.query(Notification)
        .outerjoin(
            NotificationDeliveryState,
            NotificationDeliveryState.notification_id == Notification.id,
        )
        .filter(
            or_(
                and_(
                    Notification.status == "pending",
                    or_(
                        NotificationDeliveryState.id.is_(None),
                        NotificationDeliveryState.next_attempt_at.is_(None),
                        NotificationDeliveryState.next_attempt_at <= now,
                    ),
                ),
                and_(
                    Notification.status == "processing",
                    or_(
                        NotificationDeliveryState.next_attempt_at.is_(None),
                        NotificationDeliveryState.next_attempt_at <= now,
                    ),
                ),
            )
        )
        .filter(
            or_(
                NotificationDeliveryState.id.is_(None),
                NotificationDeliveryState.attempts < MAX_ATTEMPTS,
            )
        )
        .order_by(Notification.created_at.asc(), Notification.id.asc())
        .with_for_update(of=Notification, skip_locked=True)
        .limit(batch_size)
        .all()
    )

    claimed: list[dict] = []
    lease_until = now + timedelta(seconds=LEASE_SECONDS)
    for row in rows:
        state = (
            db.query(NotificationDeliveryState)
            .filter(NotificationDeliveryState.notification_id == row.id)
            .with_for_update()
            .first()
        )
        if not state:
            state = NotificationDeliveryState(
                notification_id=row.id,
                attempts=0,
            )
            db.add(state)
            db.flush()

        lease_token = uuid.uuid4().hex
        row.status = "processing"
        state.next_attempt_at = lease_until
        state.lease_token = lease_token
        state.updated_at = now
        claimed.append(
            {
                "id": row.id,
                "telegram_id": row.telegram_id,
                "message": row.message,
                "lease_until": lease_until,
                "lease_token": lease_token,
            }
        )

    db.commit()
    return claimed


def renew_delivery_lease(
    db: Session,
    notification_id: int,
    lease_token: str,
) -> bool:
    normalized_token = str(lease_token or "").strip()
    if not normalized_token:
        return False

    row = (
        db.query(Notification)
        .filter(
            Notification.id == notification_id,
            Notification.status == "processing",
        )
        .with_for_update()
        .first()
    )
    if not row or row.status != "processing":
        db.rollback()
        return False

    state = (
        db.query(NotificationDeliveryState)
        .filter(
            NotificationDeliveryState.notification_id == row.id,
            NotificationDeliveryState.lease_token == normalized_token,
        )
        .with_for_update()
        .first()
    )
    if not state or state.lease_token != normalized_token:
        db.rollback()
        return False

    now = utcnow_naive()
    state.next_attempt_at = now + timedelta(seconds=LEASE_SECONDS)
    state.updated_at = now
    db.commit()
    return True


def finish_delivery(
    db: Session,
    notification_id: int,
    lease_token: str,
    error: Exception | None = None,
) -> str:
    normalized_token = str(lease_token or "").strip()
    if not normalized_token:
        return "ignored"

    row = (
        db.query(Notification)
        .filter(
            Notification.id == notification_id,
            Notification.status == "processing",
        )
        .with_for_update()
        .first()
    )
    if not row or row.status != "processing":
        db.rollback()
        return "ignored"

    state = (
        db.query(NotificationDeliveryState)
        .filter(
            NotificationDeliveryState.notification_id == row.id,
            NotificationDeliveryState.lease_token == normalized_token,
        )
        .with_for_update()
        .first()
    )
    if not state or state.lease_token != normalized_token:
        db.rollback()
        return "ignored"

    now = utcnow_naive()
    if error is None:
        row.status = "sent"
        row.sent_at = now
        row.error = ""
        db.delete(state)
        db.commit()
        return "sent"

    state.attempts = max(int(state.attempts or 0), 0) + 1
    state.updated_at = now
    state.last_error = f"{error.__class__.__name__}: {error}"[:2000]
    state.lease_token = None
    row.error = state.last_error
    if state.attempts >= MAX_ATTEMPTS:
        row.status = "failed"
        state.next_attempt_at = None
        outcome = "failed"
    else:
        row.status = "pending"
        state.next_attempt_at = next_attempt_at(state.attempts, now=now)
        outcome = "retry_scheduled"
    db.commit()
    return outcome
