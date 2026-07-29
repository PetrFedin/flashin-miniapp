from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

from backend.models import Notification
from backend.notification_models import NotificationDeliveryState
from backend.services.notification_delivery import reset_notification_delivery


def _notification(status: str) -> Notification:
    return Notification(
        id=101,
        telegram_id="123456",
        message="Order update",
        status=status,
        error="delivery failed",
        sent_at=datetime.utcnow() if status == "sent" else None,
    )


def test_failed_notification_is_reset_for_immediate_retry():
    notification = _notification("failed")
    state = NotificationDeliveryState(
        id=7,
        notification_id=notification.id,
        attempts=5,
        next_attempt_at=None,
        last_error="network error",
    )
    reset_at = datetime.utcnow() - timedelta(seconds=1)

    result = reset_notification_delivery(notification, state, now=reset_at)

    assert result is state
    assert notification.status == "pending"
    assert notification.error == ""
    assert notification.sent_at is None
    assert state.attempts == 0
    assert state.next_attempt_at == reset_at
    assert state.last_error == ""
    assert state.updated_at == reset_at


def test_pending_notification_without_state_gets_new_retry_state():
    notification = _notification("pending")
    reset_at = datetime.utcnow()

    state = reset_notification_delivery(notification, now=reset_at)

    assert state.notification_id == notification.id
    assert state.attempts == 0
    assert state.next_attempt_at == reset_at
    assert notification.status == "pending"


def test_sent_notification_cannot_be_replayed():
    notification = _notification("sent")

    with pytest.raises(HTTPException) as exc_info:
        reset_notification_delivery(notification)

    assert exc_info.value.status_code == 409
    assert "cannot be retried" in str(exc_info.value.detail)
