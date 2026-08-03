#!/usr/bin/env python3
"""Verify atomic and retry-safe BusinessEvent dispatch on PostgreSQL.

The worker processes one healthy event and one poison event. The poison handler
creates a real webhook outbox row and then raises. A per-event savepoint must
remove that partial outbox row while preserving the successful event and the
poison event's retry counter.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy.orm import Session

from backend.database import engine
from backend.models import BusinessEvent, WebhookDestination, WebhookOutbox
from backend.services import event_dispatcher


def main() -> int:
    token = uuid.uuid4().hex[:20]
    healthy_type = f"smoke.event.healthy.{token}"
    poison_type = f"smoke.event.poison.{token}"
    connection = engine.connect()
    outer_transaction = connection.begin()
    db = Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    original_enqueue = event_dispatcher.enqueue_event_for_destinations

    try:
        destination = WebhookDestination(
            name=f"Business event smoke {token}",
            url=f"https://example.com/flashin-event-smoke/{token}",
            event_type="*",
            active=True,
            signing_secret="",
        )
        db.add(destination)
        healthy = event_dispatcher.emit_event(
            db,
            healthy_type,
            aggregate_type="smoke",
            aggregate_id=f"healthy-{token}",
            payload={"token": token, "kind": "healthy"},
        )
        poison = event_dispatcher.emit_event(
            db,
            poison_type,
            aggregate_type="smoke",
            aggregate_id=f"poison-{token}",
            payload={"token": token, "kind": "poison"},
        )
        db.flush()
        healthy_id = healthy.id
        poison_id = poison.id
        destination_id = destination.id
        db.commit()

        def fail_after_outbox(db_session: Session, event_type: str, payload: dict) -> int:
            created = original_enqueue(db_session, event_type, payload)
            if event_type == poison_type:
                raise RuntimeError("intentional poison event after outbox creation")
            return created

        event_dispatcher.enqueue_event_for_destinations = fail_after_outbox

        for invalid_limit in (True, 0, -1, 1001):
            try:
                event_dispatcher.process_pending_events(db, limit=invalid_limit)
            except ValueError:
                pass
            else:
                raise AssertionError(
                    f"Invalid event batch limit was accepted: {invalid_limit!r}"
                )

        first_processed = event_dispatcher.process_pending_events(db, limit=2)
        assert first_processed == 1

        db.expire_all()
        healthy = db.query(BusinessEvent).filter(BusinessEvent.id == healthy_id).one()
        poison = db.query(BusinessEvent).filter(BusinessEvent.id == poison_id).one()
        first_outboxes = (
            db.query(WebhookOutbox)
            .filter(WebhookOutbox.event_type.in_([healthy_type, poison_type]))
            .order_by(WebhookOutbox.id.asc())
            .all()
        )

        assert healthy.status == "processed"
        assert healthy.attempts == 0
        assert healthy.processed_at is not None
        assert poison.status == "pending"
        assert poison.attempts == 1
        assert poison.processed_at is None
        assert len(first_outboxes) == 1
        assert first_outboxes[0].event_type == healthy_type
        assert first_outboxes[0].status == "pending"
        assert json.loads(first_outboxes[0].payload) == {
            "token": token,
            "kind": "healthy",
        }

        for expected_attempt in range(2, 11):
            processed = event_dispatcher.process_pending_events(db, limit=1)
            assert processed == 0
            db.expire_all()
            poison = db.query(BusinessEvent).filter(BusinessEvent.id == poison_id).one()
            assert poison.attempts == expected_attempt
            expected_status = "failed" if expected_attempt == 10 else "pending"
            assert poison.status == expected_status
            assert poison.processed_at is None
            assert (
                db.query(WebhookOutbox)
                .filter(WebhookOutbox.event_type == poison_type)
                .count()
                == 0
            )

        event_dispatcher.enqueue_event_for_destinations = original_enqueue
        final_processed = event_dispatcher.process_pending_events(db, limit=100)
        assert final_processed == 0

        db.expire_all()
        healthy = db.query(BusinessEvent).filter(BusinessEvent.id == healthy_id).one()
        poison = db.query(BusinessEvent).filter(BusinessEvent.id == poison_id).one()
        outboxes = (
            db.query(WebhookOutbox)
            .filter(WebhookOutbox.event_type.in_([healthy_type, poison_type]))
            .all()
        )
        persisted_destination = (
            db.query(WebhookDestination)
            .filter(WebhookDestination.id == destination_id)
            .one()
        )

        assert healthy.status == "processed"
        assert poison.status == "failed"
        assert poison.attempts == 10
        assert len(outboxes) == 1
        assert outboxes[0].event_type == healthy_type
        assert persisted_destination.active is True

        print(
            json.dumps(
                {
                    "status": "ok",
                    "healthy_event": healthy.status,
                    "poison_event": poison.status,
                    "poison_attempts": poison.attempts,
                    "persisted_outboxes": len(outboxes),
                    "partial_poison_outboxes": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        event_dispatcher.enqueue_event_for_destinations = original_enqueue
        db.close()
        if outer_transaction.is_active:
            outer_transaction.rollback()
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
