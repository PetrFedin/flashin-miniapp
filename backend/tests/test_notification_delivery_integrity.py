from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import Notification
from backend.notification_models import NotificationDeliveryState
from backend.notification_statuses import MAX_NOTIFICATION_ATTEMPTS
from backend.services.notification_delivery import (
    claim_notification_delivery,
    classify_notification_error,
    complete_notification_delivery,
)
from backend.services.notifications import queue_notification


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _notification(status: str = "pending") -> Notification:
    return Notification(
        id=101,
        telegram_id="123456",
        message="Order update",
        status=status,
        error="",
        sent_at=None,
    )


def _state(**overrides) -> NotificationDeliveryState:
    values = {
        "id": 7,
        "notification_id": 101,
        "attempts": 0,
        "next_attempt_at": datetime.utcnow(),
        "last_error": "",
        "deduplication_key": "",
        "lease_token": "",
    }
    values.update(overrides)
    return NotificationDeliveryState(**values)


def test_queue_notification_normalizes_and_deduplicates_business_event():
    db = _session()

    assert queue_notification(
        db,
        " 00123456 ",
        "  Order paid  ",
        deduplication_key=" order:42:paid ",
    )
    assert not queue_notification(
        db,
        "123456",
        "Order paid again",
        deduplication_key="order:42:paid",
    )
    db.commit()

    notification = db.query(Notification).one()
    state = db.query(NotificationDeliveryState).one()
    assert notification.telegram_id == "123456"
    assert notification.message == "Order paid"
    assert notification.status == "pending"
    assert state.notification_id == notification.id
    assert state.deduplication_key == "order:42:paid"
    assert state.next_attempt_at is not None


def test_invalid_notification_is_not_queued():
    db = _session()

    assert not queue_notification(db, "0", "message")
    assert not queue_notification(db, "123", "   ")
    assert db.query(Notification).count() == 0


def test_claim_uses_fencing_token_and_stale_completion_is_ignored():
    now = datetime.utcnow()
    notification = _notification()
    state = _state(next_attempt_at=now)

    lease_token = claim_notification_delivery(
        notification,
        state,
        now=now,
        lease_seconds=60,
        max_attempts=5,
    )

    assert lease_token
    assert notification.status == "processing"
    assert state.lease_token == lease_token
    assert state.next_attempt_at == now + timedelta(seconds=60)
    assert complete_notification_delivery(
        notification,
        state,
        "stale-worker-token",
        now=now,
    ) == "ignored"
    assert notification.status == "processing"


def test_transient_error_retries_then_success_preserves_attempt_audit():
    started_at = datetime.utcnow()
    notification = _notification()
    state = _state(next_attempt_at=started_at)
    first_token = claim_notification_delivery(notification, state, now=started_at)

    outcome = complete_notification_delivery(
        notification,
        state,
        first_token,
        TimeoutError("Telegram timeout"),
        now=started_at,
        max_attempts=5,
        initial_backoff_seconds=30,
        max_backoff_seconds=300,
    )

    assert outcome == "retry_scheduled"
    assert notification.status == "pending"
    assert notification.sent_at is None
    assert state.attempts == 1
    assert state.next_attempt_at == started_at + timedelta(seconds=30)
    assert "TimeoutError" in state.last_error

    retry_at = state.next_attempt_at
    second_token = claim_notification_delivery(notification, state, now=retry_at)
    assert complete_notification_delivery(
        notification,
        state,
        second_token,
        now=retry_at,
    ) == "sent"
    assert notification.status == "sent"
    assert notification.sent_at == retry_at
    assert notification.error == ""
    assert state.attempts == 1
    assert "TimeoutError" in state.last_error
    assert state.next_attempt_at is None
    assert state.lease_token == ""


def test_permanent_delivery_error_fails_without_automatic_retry():
    now = datetime.utcnow()
    notification = _notification()
    state = _state(next_attempt_at=now)
    token = claim_notification_delivery(notification, state, now=now)

    outcome = complete_notification_delivery(
        notification,
        state,
        token,
        ValueError("Telegram chat id is invalid"),
        now=now,
        max_attempts=5,
    )

    assert outcome == "failed"
    assert notification.status == "failed"
    assert notification.sent_at is None
    assert state.attempts == 1
    assert state.next_attempt_at is None
    assert state.lease_token == ""


def test_expired_processing_lease_counts_toward_terminal_limit():
    now = datetime.utcnow()
    notification = _notification("processing")
    notification.error = "previous error"
    state = _state(
        attempts=4,
        next_attempt_at=now - timedelta(seconds=1),
        last_error="previous error",
        lease_token="old-token",
    )

    token = claim_notification_delivery(
        notification,
        state,
        now=now,
        max_attempts=5,
    )

    assert token is None
    assert notification.status == "failed"
    assert state.attempts == 5
    assert state.next_attempt_at is None
    assert state.lease_token == ""
    assert "lease expired" in state.last_error.lower()


def test_retry_after_is_respected_and_bounded():
    class TelegramRetryAfter(Exception):
        retry_after = 90

    decision = classify_notification_error(TelegramRetryAfter("flood control"))

    assert decision.retryable is True
    assert decision.retry_after_seconds == 90


def test_unknown_programming_error_is_not_replayed_automatically():
    class UnexpectedContractError(Exception):
        pass

    decision = classify_notification_error(UnexpectedContractError("bad provider contract"))

    assert decision.retryable is False
    assert decision.retry_after_seconds is None


@pytest.mark.parametrize(
    "values",
    [
        {
            "telegram_id": "123",
            "message": "message",
            "status": "unknown",
            "error": "",
            "sent_at": None,
        },
        {
            "telegram_id": "123",
            "message": "message",
            "status": "sent",
            "error": "",
            "sent_at": None,
        },
        {
            "telegram_id": "123",
            "message": "message",
            "status": "failed",
            "error": "",
            "sent_at": None,
        },
        {
            "telegram_id": "123",
            "message": "x" * 4097,
            "status": "pending",
            "error": "",
            "sent_at": None,
        },
    ],
)
def test_direct_sql_rejects_invalid_notification_states(values):
    db = _session()

    with pytest.raises(IntegrityError):
        db.execute(Notification.__table__.insert().values(**values))
        db.commit()
    db.rollback()


@pytest.mark.parametrize(
    "values",
    [
        {
            "notification_id": 1,
            "attempts": MAX_NOTIFICATION_ATTEMPTS + 1,
            "next_attempt_at": None,
            "last_error": "too many attempts",
            "deduplication_key": "",
            "lease_token": "",
        },
        {
            "notification_id": 1,
            "attempts": 0,
            "next_attempt_at": None,
            "last_error": "",
            "deduplication_key": "",
            "lease_token": "active-without-deadline",
        },
        {
            "notification_id": 1,
            "attempts": 1,
            "next_attempt_at": None,
            "last_error": "",
            "deduplication_key": "",
            "lease_token": "",
        },
    ],
)
def test_direct_sql_rejects_invalid_delivery_state(values):
    db = _session()
    db.execute(
        Notification.__table__.insert().values(
            id=1,
            telegram_id="123",
            message="message",
            status="pending",
            error="",
            sent_at=None,
        )
    )

    with pytest.raises(IntegrityError):
        db.execute(NotificationDeliveryState.__table__.insert().values(**values))
        db.commit()
    db.rollback()


def test_orm_rejects_terminal_notification_without_error():
    db = _session()
    db.add(
        Notification(
            telegram_id="123",
            message="message",
            status="failed",
            error="",
            sent_at=None,
        )
    )

    with pytest.raises(HTTPException):
        db.commit()
    db.rollback()


def test_migration_repairs_rows_before_enabling_constraints():
    source = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0028_notification_delivery_integrity.py"
    ).read_text(encoding="utf-8")

    first_repair = source.index("UPDATE notifications")
    first_constraint = source.index("op.create_check_constraint")
    state_backfill = source.index("INSERT INTO notification_delivery_states")

    assert first_repair < state_backfill < first_constraint
    assert "Recovered expired legacy processing lease" in source
    assert "uq_notification_delivery_states_deduplication_key" in source
    assert 'down_revision = "0027_webhook_payload_limits"' in source
