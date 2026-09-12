#!/usr/bin/env python3
"""Prove Telegram notification transport safety with real PostgreSQL state.

PostgreSQL claim/lease/policy/finalize logic and
bot.send_notifications.send_pending_batch are real. Only Telegram network I/O
is replaced with a deterministic Bot-like transport. The smoke proves:
- transaction-clean handoff at the actual Telegram side-effect boundary;
- successful transactional delivery;
- ambiguous accepted-but-response-lost delivery parks in review_required and
  is not automatically replayed;
- grant -> enqueue -> revoke suppresses marketing before Telegram I/O.
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
    def __init__(self, db: Session) -> None:
        self.db = db
        self.calls: list[dict] = []
        self.failure: Exception | None = None
        self.transaction_clean_checks = 0

    async def send_message(self, **kwargs):
        # The exact transport boundary must never inherit a SQLAlchemy
        # transaction from claim/lease/preflight work.
        assert self.db.in_transaction() is False
        self.transaction_clean_checks += 1
        self.calls.append(dict(kwargs))
        if self.failure is not None:
            raise self.failure
        return {"message_id": len(self.calls)}


def _empty_result() -> dict[str, int]:
    return {
        "seen": 0,
        "sent": 0,
        "retry_scheduled": 0,
        "failed": 0,
        "review_required": 0,
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
            lambda row_id, lease_token, error=None, delivery_outcome=None: finish_delivery(
                db,
                row_id,
                lease_token,
                error=error,
                delivery_outcome=delivery_outcome,
            )
        )

        bot = RecordingBot(db)
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
        assert bot.transaction_clean_checks == 1

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
        db.rollback()

        # Prove the P1 recovery boundary: model Telegram accepting the side effect
        # and then losing the response. A generic transport exception is therefore
        # ambiguous and must never trigger a blind automatic resend.
        ambiguous_notification = Notification(
            telegram_id=str(int(token, 16) + 1),
            message=f"FLASHIN ambiguous transport smoke {token}",
            status="pending",
        )
        db.add(ambiguous_notification)
        db.commit()
        ambiguous_notification_id = int(ambiguous_notification.id)
        bot.failure = RuntimeError("simulated response loss after provider acceptance")

        ambiguous = asyncio.run(worker.send_pending_batch(bot))
        expected_ambiguous = _empty_result()
        expected_ambiguous.update({"seen": 1, "review_required": 1})
        assert ambiguous == expected_ambiguous
        assert len(bot.calls) == 2
        assert bot.transaction_clean_checks == 2

        db.expire_all()
        ambiguous_persisted = (
            db.query(Notification)
            .filter(Notification.id == ambiguous_notification_id)
            .one()
        )
        ambiguous_state = (
            db.query(NotificationDeliveryState)
            .filter(NotificationDeliveryState.notification_id == ambiguous_notification_id)
            .one()
        )
        assert ambiguous_persisted.status == "review_required"
        assert ambiguous_persisted.sent_at is None
        assert "RuntimeError" in ambiguous_persisted.error
        assert ambiguous_state.attempts == 1
        assert ambiguous_state.next_attempt_at is None
        assert ambiguous_state.lease_token is None
        db.rollback()

        bot.failure = None
        replay_after_ambiguous = asyncio.run(worker.send_pending_batch(bot))
        assert replay_after_ambiguous == _empty_result()
        assert len(bot.calls) == 2

        # Now prove consent was valid at enqueue, then withdrawn before the
        # transport attempt. The worker must terminally suppress it without
        # invoking Telegram.
        marketing_telegram_id = str(int(token, 16) + 2)
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
        assert len(bot.calls) == 2

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
        db.rollback()

        replay = asyncio.run(worker.send_pending_batch(bot))
        assert replay == _empty_result()
        assert len(bot.calls) == 2

        print(
            json.dumps(
                {
                    "status": "ok",
                    "transactional_notification_id": notification_id,
                    "transactional_final_status": persisted.status,
                    "ambiguous_notification_id": ambiguous_notification_id,
                    "ambiguous_final_status": ambiguous_persisted.status,
                    "ambiguous_auto_replay_seen": replay_after_ambiguous["seen"],
                    "marketing_notification_id": marketing_notification_id,
                    "marketing_final_status": marketing_notification.status,
                    "telegram_send_calls": len(bot.calls),
                    "transaction_clean_checks": bot.transaction_clean_checks,
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
