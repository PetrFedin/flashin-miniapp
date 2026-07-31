from datetime import datetime

from fastapi import HTTPException

from ..database import utcnow_naive
from ..models import Notification
from ..notification_models import NotificationDeliveryState


RETRYABLE_NOTIFICATION_STATUSES = {"pending", "failed"}


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
    state.last_error = ""
    state.updated_at = reset_at
    return state
