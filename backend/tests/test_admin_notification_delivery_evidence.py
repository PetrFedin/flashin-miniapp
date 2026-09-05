from datetime import datetime
from types import SimpleNamespace

from backend.api.admin_notifications import _serialize


def test_notification_delivery_evidence_exposes_event_key():
    notification = SimpleNamespace(
        id=17,
        telegram_id="123456789",
        message="refund delivered",
        status="sent",
        error="",
        created_at=datetime(2026, 8, 11, 10, 0, 0),
        sent_at=datetime(2026, 8, 11, 10, 1, 0),
    )
    state = None
    event = SimpleNamespace(event_key="order:42:refund:71:succeeded")

    payload = _serialize(notification, state, event)

    assert payload["event_key"] == "order:42:refund:71:succeeded"
    assert payload["status"] == "sent"
    assert payload["sent_at"] == datetime(2026, 8, 11, 10, 1, 0)
    assert payload["attempts"] == 0
    assert payload["next_attempt_at"] is None
    assert payload["last_error"] == ""


def test_notification_delivery_evidence_keeps_event_key_optional():
    notification = SimpleNamespace(
        id=18,
        telegram_id="123456789",
        message="legacy notification",
        status="pending",
        error="",
        created_at=datetime(2026, 8, 11, 10, 0, 0),
        sent_at=None,
    )

    payload = _serialize(notification, None)

    assert payload["event_key"] == ""
