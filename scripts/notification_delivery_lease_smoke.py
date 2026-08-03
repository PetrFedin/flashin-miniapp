#!/usr/bin/env python3
"""Prove stale Telegram notification workers cannot send-finalize reclaimed rows."""

from __future__ import annotations

import json
import sys
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import engine, utcnow_naive
from backend.models import Notification
from backend.notification_models import NotificationDeliveryState
from bot.send_notifications import (
    _claim_pending_batch_db,
    _finish_delivery_db,
    _renew_delivery_lease_db,
)


def _state(db: Session, notification_id: int) -> tuple[Notification, NotificationDeliveryState | None]:
    db.expire_all()
    notification = (
        db.query(Notification)
        .filter(Notification.id == notification_id)
        .one()
    )
    delivery = (
        db.query(NotificationDeliveryState)
        .filter(NotificationDeliveryState.notification_id == notification_id)
        .first()
    )
    return notification, delivery


def _make_due(db: Session, notification_id: int) -> None:
    delivery = (
        db.query(NotificationDeliveryState)
        .filter(NotificationDeliveryState.notification_id == notification_id)
        .with_for_update()
        .one()
    )
    delivery.next_attempt_at = utcnow_naive() - timedelta(seconds=1)
    db.commit()


def main() -> int:
    token = uuid.uuid4().hex[:20]
    connection = engine.connect()
    outer_transaction = connection.begin()
    db = Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    try:
        notification = Notification(
            telegram_id=str(int(token, 16)),
            message=f"Owned notification lease smoke {token}",
            status="pending",
            error="",
        )
        db.add(notification)
        db.commit()
        notification_id = notification.id

        first_claim = _claim_pending_batch_db(db, limit=1)
        assert len(first_claim) == 1
        assert first_claim[0]["id"] == notification_id
        first_token = first_claim[0]["lease_token"]
        assert isinstance(first_token, str) and len(first_token) == 32

        first_notification, first_state = _state(db, notification_id)
        assert first_notification.status == "processing"
        assert first_state is not None
        assert first_state.attempts == 0
        assert first_state.lease_token == first_token
        assert first_state.next_attempt_at is not None

        assert _renew_delivery_lease_db(db, notification_id, first_token) is True
        renewed_notification, renewed_state = _state(db, notification_id)
        assert renewed_notification.status == "processing"
        assert renewed_state is not None
        assert renewed_state.lease_token == first_token

        _make_due(db, notification_id)
        second_claim = _claim_pending_batch_db(db, limit=1)
        assert len(second_claim) == 1
        assert second_claim[0]["id"] == notification_id
        second_token = second_claim[0]["lease_token"]
        assert second_token != first_token

        assert _renew_delivery_lease_db(db, notification_id, first_token) is False
        assert (
            _finish_delivery_db(
                db,
                notification_id,
                first_token,
                error=RuntimeError("stale worker failure must be ignored"),
            )
            == "ignored"
        )
        assert (
            _finish_delivery_db(
                db,
                notification_id,
                first_token,
                error=None,
            )
            == "ignored"
        )

        after_stale_notification, after_stale_state = _state(db, notification_id)
        assert after_stale_notification.status == "processing"
        assert after_stale_notification.sent_at is None
        assert after_stale_notification.error == ""
        assert after_stale_state is not None
        assert after_stale_state.attempts == 0
        assert after_stale_state.last_error == ""
        assert after_stale_state.lease_token == second_token

        assert (
            _finish_delivery_db(
                db,
                notification_id,
                second_token,
                error=RuntimeError("active worker transient failure"),
            )
            == "retry_scheduled"
        )
        retry_notification, retry_state = _state(db, notification_id)
        assert retry_notification.status == "pending"
        assert retry_notification.sent_at is None
        assert retry_notification.error == "RuntimeError: active worker transient failure"
        assert retry_state is not None
        assert retry_state.attempts == 1
        assert retry_state.last_error == "RuntimeError: active worker transient failure"
        assert retry_state.lease_token is None
        assert retry_state.next_attempt_at is not None

        _make_due(db, notification_id)
        third_claim = _claim_pending_batch_db(db, limit=1)
        assert len(third_claim) == 1
        assert third_claim[0]["id"] == notification_id
        third_token = third_claim[0]["lease_token"]
        assert third_token not in {first_token, second_token}

        assert _renew_delivery_lease_db(db, notification_id, third_token) is True
        assert (
            _finish_delivery_db(
                db,
                notification_id,
                third_token,
                error=None,
            )
            == "sent"
        )

        final_notification, final_state = _state(db, notification_id)
        assert final_notification.status == "sent"
        assert final_notification.sent_at is not None
        assert final_notification.error == ""
        assert final_state is None

        assert _renew_delivery_lease_db(db, notification_id, third_token) is False
        assert (
            _finish_delivery_db(
                db,
                notification_id,
                third_token,
                error=None,
            )
            == "ignored"
        )
        assert _claim_pending_batch_db(db, limit=1) == []

        for invalid_limit in (0, 201, True, 1.5):
            try:
                _claim_pending_batch_db(db, limit=invalid_limit)  # type: ignore[arg-type]
            except ValueError:
                pass
            else:
                raise AssertionError(
                    f"Invalid notification batch size was accepted: {invalid_limit!r}"
                )

        print(
            json.dumps(
                {
                    "status": "ok",
                    "notification_id": notification_id,
                    "lease_rotations": 3,
                    "stale_finish_rejected": True,
                    "retry_attempts": 1,
                    "final_status": final_notification.status,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        db.close()
        if outer_transaction.is_active:
            outer_transaction.rollback()
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
