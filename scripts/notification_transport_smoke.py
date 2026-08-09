#!/usr/bin/env python3
"""Prove the notification worker handoff through Telegram sendMessage.

PostgreSQL claim/lease/finalize logic and bot.send_notifications.send_pending_batch
are real. Only Telegram network I/O is replaced with a deterministic Bot-like
transport that records send_message calls. A replay must not resend the message.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.database import engine
from backend.models import Notification
from backend.notification_models import NotificationDeliveryState
from backend.services.notification_delivery import (
    claim_pending_batch,
    finish_delivery,
    renew_delivery_lease,
)
from bot import send_notifications as worker


class RecordingBot:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def send_message(self, **kwargs):
        self.calls.append(dict(kwargs))
        return {"message_id": len(self.calls)}


def main() -> int:
    token = uuid.uuid4().hex[:16]
    connection = engine.connect()
    outer_transaction = connection.begin()
    db = Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    original_claim = worker._claim_pending_batch
    original_renew = worker._renew_delivery_lease
    original_finish = worker._finish_delivery

    try:
        telegram_id = str(int(token, 16))
        message = f"FLASHIN pilot transport smoke {token}"
        notification = Notification(
            telegram_id=telegram_id,
            message=message,
            status="pending",
        )
        db.add(notification)
        db.commit()
        notification_id = int(notification.id)

        worker._claim_pending_batch = lambda: claim_pending_batch(db, 10)
        worker._renew_delivery_lease = (
            lambda row_id, lease_token: renew_delivery_lease(db, row_id, lease_token)
        )
        worker._finish_delivery = (
            lambda row_id, lease_token, error=None: finish_delivery(
                db,
                row_id,
                lease_token,
                error=error,
            )
        )

        bot = RecordingBot()
        first = asyncio.run(worker.send_pending_batch(bot))
        assert first == {
            "seen": 1,
            "sent": 1,
            "retry_scheduled": 0,
            "failed": 0,
            "ignored": 0,
        }
        assert bot.calls == [
            {
                "chat_id": int(telegram_id),
                "text": message,
                "disable_web_page_preview": True,
            }
        ]

        db.expire_all()
        persisted = db.query(Notification).filter(Notification.id == notification_id).one()
        assert persisted.status == "sent"
        assert persisted.sent_at is not None
        assert persisted.error == ""
        assert (
            db.query(NotificationDeliveryState)
            .filter(NotificationDeliveryState.notification_id == notification_id)
            .count()
            == 0
        )

        replay = asyncio.run(worker.send_pending_batch(bot))
        assert replay == {
            "seen": 0,
            "sent": 0,
            "retry_scheduled": 0,
            "failed": 0,
            "ignored": 0,
        }
        assert len(bot.calls) == 1

        print(
            json.dumps(
                {
                    "status": "ok",
                    "notification_id": notification_id,
                    "telegram_send_calls": len(bot.calls),
                    "final_status": persisted.status,
                    "replay_seen": replay["seen"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        worker._claim_pending_batch = original_claim
        worker._renew_delivery_lease = original_renew
        worker._finish_delivery = original_finish
        db.close()
        if outer_transaction.is_active:
            outer_transaction.rollback()
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
