#!/usr/bin/env python3
"""Prove notification handoff and marketing-consent suppression at transport.

PostgreSQL claim/lease/policy/finalize logic and
bot.send_notifications.send_pending_batch are real. Only Telegram network I/O
is replaced with a deterministic Bot-like transport. The smoke proves both a
transactional delivery and grant -> enqueue -> revoke -> no-send behavior.
"""

from __future__ import annotations

import asyncio
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
from backend.models import ConsentRecord, Customer, Notification
from backend.notification_models import NotificationDeliveryState, NotificationPolicyContext
from backend.services.notification_delivery import (
    claim_pending_batch,
    finish_delivery,
    preflight_notification_delivery,
    renew_delivery_lease,
)
from backend.services.notifications import (
    NOTIFICATION_PURPOSE_MARKETING,
    queue_notification,
)
from bot import send_notifications as worker


class RecordingBot:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def send_message(self, **kwargs):
        self.calls.append(dict(kwargs))
        return {"message_id": len(self.calls)}


def _empty_result() -> dict[str, int]:
    return {
        "seen": 0,
        "sent": 0,
        "retry_scheduled": 0,
        "failed": 0,
        "suppressed": 0,
        "ignored": 0,
    }


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
    original_preflight = worker._preflight_notification_delivery
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

        # Keep every worker DB operation on this smoke's savepoint-bound Session.
        # A second engine Session cannot see rows hidden by the outer transaction.
        worker._claim_pending_batch = lambda: claim_pending_batch(db, 10)
        worker._renew_delivery_lease = (
            lambda row_id, lease_token: renew_delivery_lease(db, row_id, lease_token)
        )
        worker._preflight_notification_delivery = (
            lambda row_id, lease_token: preflight_notification_delivery(
                db,
                row_id,
                lease_token,
            )
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
        expected_first = _empty_result()
        expected_first.update({"seen": 1, "sent": 1})
        assert first == expected_first
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

        # Now prove the exact P1: consent was valid at enqueue, then withdrawn
        # before the transport attempt. The worker must terminally suppress it
        # without invoking Telegram.
        marketing_telegram_id = str(int(token, 16) + 1)
        customer = Customer(telegram_id=marketing_telegram_id)
        db.add(customer)
        db.flush()
        granted_at = utcnow_naive() - timedelta(minutes=2)
        db.add(
            ConsentRecord(
                customer_id=customer.id,
                consent_type="marketing",
                granted=True,
                source="notification_transport_smoke",
                created_at=granted_at,
            )
        )
        db.flush()
        assert queue_notification(
            db,
            marketing_telegram_id,
            f"FLASHIN marketing transport smoke {token}",
            purpose=NOTIFICATION_PURPOSE_MARKETING,
            customer_id=customer.id,
        )
        db.flush()
        marketing_context = (
            db.query(NotificationPolicyContext)
            .filter(NotificationPolicyContext.customer_id == customer.id)
            .one()
        )
        marketing_notification_id = int(marketing_context.notification_id)
        db.add(
            ConsentRecord(
                customer_id=customer.id,
                consent_type="marketing",
                granted=False,
                source="notification_transport_smoke",
                created_at=utcnow_naive() - timedelta(minutes=1),
            )
        )
        db.commit()

        suppressed = asyncio.run(worker.send_pending_batch(bot))
        expected_suppressed = _empty_result()
        expected_suppressed.update({"seen": 1, "suppressed": 1})
        assert suppressed == expected_suppressed
        assert len(bot.calls) == 1

        db.expire_all()
        marketing_notification = (
            db.query(Notification)
            .filter(Notification.id == marketing_notification_id)
            .one()
        )
        assert marketing_notification.status == "suppressed"
        assert marketing_notification.sent_at is None
        assert "consent" in marketing_notification.error.lower()
        assert (
            db.query(NotificationDeliveryState)
            .filter(NotificationDeliveryState.notification_id == marketing_notification_id)
            .count()
            == 0
        )

        replay = asyncio.run(worker.send_pending_batch(bot))
        assert replay == _empty_result()
        assert len(bot.calls) == 1

        print(
            json.dumps(
                {
                    "status": "ok",
                    "transactional_notification_id": notification_id,
                    "transactional_final_status": persisted.status,
                    "marketing_notification_id": marketing_notification_id,
                    "marketing_final_status": marketing_notification.status,
                    "telegram_send_calls": len(bot.calls),
                    "marketing_suppressed": suppressed["suppressed"],
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
        worker._preflight_notification_delivery = original_preflight
        worker._finish_delivery = original_finish
        db.close()
        if outer_transaction.is_active:
            outer_transaction.rollback()
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
