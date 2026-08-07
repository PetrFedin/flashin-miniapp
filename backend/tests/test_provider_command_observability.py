from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.provider_models import ProviderCommand
from backend.services.provider_observability import build_provider_command_status


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _command(*, status: str, created_at: datetime, next_attempt_at=None, key: str):
    return ProviderCommand(
        provider="moysklad",
        command_type="moysklad.customer_order.create",
        idempotency_key=key,
        aggregate_type="order",
        aggregate_id=key,
        payload_json="{}",
        status=status,
        attempts=0,
        next_attempt_at=next_attempt_at,
        created_at=created_at,
    )


def test_provider_command_snapshot_counts_due_leases_and_oldest_age():
    db = _session()
    now = datetime(2026, 8, 7, 12, 0, 0)
    db.add_all(
        [
            _command(
                status="pending",
                created_at=now - timedelta(minutes=10),
                next_attempt_at=None,
                key="pending-001",
            ),
            _command(
                status="pending",
                created_at=now - timedelta(minutes=2),
                next_attempt_at=now + timedelta(minutes=5),
                key="pending-002",
            ),
            _command(
                status="processing",
                created_at=now - timedelta(minutes=8),
                next_attempt_at=now - timedelta(minutes=1),
                key="processing-001",
            ),
            _command(
                status="review_required",
                created_at=now - timedelta(minutes=15),
                key="review-001",
            ),
            _command(
                status="failed",
                created_at=now - timedelta(minutes=20),
                key="failed-001",
            ),
            _command(
                status="sent",
                created_at=now - timedelta(hours=3),
                key="sent-001",
            ),
        ]
    )
    db.commit()

    snapshot = build_provider_command_status(db, now=now)

    assert snapshot["provider"] == "moysklad"
    assert snapshot["counts"] == {
        "pending": 2,
        "processing": 1,
        "sent": 1,
        "failed": 1,
        "review_required": 1,
    }
    assert snapshot["due_pending"] == 1
    assert snapshot["expired_processing"] == 1
    assert snapshot["actionable_count"] == 5
    assert snapshot["oldest_age_seconds"]["pending"] == 600.0
    assert snapshot["oldest_age_seconds"]["processing"] == 480.0
    assert snapshot["oldest_age_seconds"]["review_required"] == 900.0
    assert snapshot["oldest_age_seconds"]["failed"] == 1200.0
    assert snapshot["oldest_age_seconds"]["sent"] == 10800.0
    assert snapshot["oldest_actionable_age_seconds"] == 1200.0


def test_empty_provider_snapshot_is_zeroed():
    snapshot = build_provider_command_status(
        _session(),
        now=datetime(2026, 8, 7, 12, 0, 0),
    )

    assert all(value == 0 for value in snapshot["counts"].values())
    assert all(value == 0.0 for value in snapshot["oldest_age_seconds"].values())
    assert snapshot["due_pending"] == 0
    assert snapshot["expired_processing"] == 0
    assert snapshot["actionable_count"] == 0
    assert snapshot["oldest_actionable_age_seconds"] == 0.0


def test_provider_snapshot_rejects_unbounded_provider_label():
    with pytest.raises(ValueError, match="Unsupported monitored provider"):
        build_provider_command_status(_session(), provider="arbitrary-provider")
