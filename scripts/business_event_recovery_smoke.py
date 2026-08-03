#!/usr/bin/env python3
"""Verify durable BusinessEvent diagnostics and controlled manual replay."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import Session

from backend.business_event_models import BusinessEventRecoveryState
from backend.database import engine, utcnow_naive
from backend.models import BusinessEvent, WebhookDestination, WebhookOutbox
from backend.services.event_dispatcher import (
    BusinessEventPayloadError,
    BusinessEventReplayConflictError,
    process_pending_events,
    requeue_failed_event,
)


def main() -> int:
    token = uuid.uuid4().hex[:20]
    event_type = f"smoke.event.recovery.{token}"
    connection = engine.connect()
    outer_transaction = connection.begin()
    db = Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    try:
        failed_at = utcnow_naive()
        destination = WebhookDestination(
            name=f"Business event recovery smoke {token}",
            url=f"https://example.com/flashin-recovery-smoke/{token}",
            event_type=event_type,
            active=True,
            signing_secret="",
        )
        event = BusinessEvent(
            event_type=event_type,
            aggregate_type="smoke",
            aggregate_id=token,
            payload_json=json.dumps({"token": token, "version": 1}),
            status="failed",
            attempts=10,
        )
        db.add_all([destination, event])
        db.flush()
        event_id = event.id
        recovery = BusinessEventRecoveryState(
            business_event_id=event.id,
            last_error="RuntimeError: simulated terminal failure",
            last_attempt_at=failed_at,
            failed_at=failed_at,
        )
        db.add(recovery)
        db.commit()

        replayed_event, replayed_state, before = requeue_failed_event(
            db,
            event_id,
            replacement_payload={"token": token, "version": 2, "corrected": True},
            admin_id=None,
        )
        assert before["status"] == "failed"
        assert before["attempts"] == 10
        assert before["payload_replaced"] is True
        assert replayed_event.status == "pending"
        assert replayed_event.attempts == 0
        assert replayed_state.replay_count == 1
        assert replayed_state.failed_at is None
        assert replayed_state.resolved_at is None
        db.commit()

        try:
            requeue_failed_event(db, event_id)
        except BusinessEventReplayConflictError:
            db.rollback()
        else:
            raise AssertionError("A pending event was replayed twice")

        malformed = BusinessEvent(
            event_type=f"{event_type}.malformed",
            aggregate_type="smoke",
            aggregate_id=f"malformed-{token}",
            payload_json="[]",
            status="failed",
            attempts=10,
        )
        db.add(malformed)
        db.flush()
        malformed_id = malformed.id
        db.commit()
        try:
            requeue_failed_event(db, malformed_id)
        except BusinessEventPayloadError:
            db.rollback()
        else:
            raise AssertionError("Malformed stored payload was replayed without replacement")

        processed = process_pending_events(db, limit=1000)
        assert processed >= 1
        db.expire_all()

        event = db.query(BusinessEvent).filter(BusinessEvent.id == event_id).one()
        recovery = (
            db.query(BusinessEventRecoveryState)
            .filter(BusinessEventRecoveryState.business_event_id == event_id)
            .one()
        )
        outboxes = (
            db.query(WebhookOutbox)
            .filter(WebhookOutbox.event_type == event_type)
            .all()
        )

        assert event.status == "processed"
        assert event.processed_at is not None
        assert event.attempts == 0
        assert recovery.replay_count == 1
        assert recovery.resolved_at is not None
        assert recovery.last_error == "RuntimeError: simulated terminal failure"
        assert len(outboxes) == 1
        assert json.loads(outboxes[0].payload) == {
            "token": token,
            "version": 2,
            "corrected": True,
        }

        malformed = db.query(BusinessEvent).filter(BusinessEvent.id == malformed_id).one()
        assert malformed.status == "failed"
        assert malformed.attempts == 10

        print(
            json.dumps(
                {
                    "status": "ok",
                    "event_id": event_id,
                    "final_status": event.status,
                    "replay_count": recovery.replay_count,
                    "outboxes": len(outboxes),
                    "duplicate_replay_rejected": True,
                    "malformed_replay_rejected": True,
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
