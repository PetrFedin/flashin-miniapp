from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.pilot_models import PilotWorkerHeartbeat
from backend.services.pilot_operational_safety import build_pilot_operational_safety
from backend.services.pilot_worker_heartbeat import (
    NOTIFICATION_WORKER,
    SCHEDULER_WORKER,
    build_required_worker_liveness,
    record_worker_heartbeat,
    touch_worker_heartbeat,
)


def _database():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    return factory, factory()


def _seed_healthy(db, *, opened_at: datetime, now: datetime) -> None:
    touch_worker_heartbeat(db, SCHEDULER_WORKER, now=now - timedelta(seconds=10))
    touch_worker_heartbeat(db, NOTIFICATION_WORKER, now=now - timedelta(seconds=5))
    db.commit()
    assert now - timedelta(seconds=10) >= opened_at


def test_record_worker_heartbeat_owns_a_short_transaction_and_updates_existing_row():
    factory, db = _database()
    first = datetime(2026, 8, 9, 12, 0, 0)
    second = first + timedelta(seconds=10)

    record_worker_heartbeat(SCHEDULER_WORKER, session_factory=factory, now=first)
    record_worker_heartbeat(SCHEDULER_WORKER, session_factory=factory, now=second)

    row = db.get(PilotWorkerHeartbeat, SCHEDULER_WORKER)
    assert row is not None
    assert row.last_seen_at == second
    assert db.query(PilotWorkerHeartbeat).count() == 1


def test_worker_liveness_requires_both_logical_workers_after_current_run_opened():
    _factory, db = _database()
    now = datetime(2026, 8, 9, 12, 0, 0)
    opened_at = now - timedelta(seconds=30)

    missing = build_required_worker_liveness(
        db,
        current_run_opened_at=opened_at,
        now=now,
    )
    assert missing["healthy"] is False
    assert set(missing["blocking_codes"]) == {
        "scheduler_heartbeat_missing",
        "notification_worker_heartbeat_missing",
    }

    touch_worker_heartbeat(
        db,
        SCHEDULER_WORKER,
        now=opened_at - timedelta(seconds=1),
    )
    touch_worker_heartbeat(
        db,
        NOTIFICATION_WORKER,
        now=now - timedelta(seconds=5),
    )
    db.commit()
    prearm = build_required_worker_liveness(
        db,
        current_run_opened_at=opened_at,
        now=now,
    )
    assert prearm["healthy"] is False
    assert prearm["blocking_codes"] == ["scheduler_heartbeat_precedes_current_run"]
    assert prearm["workers"][SCHEDULER_WORKER]["fresh"] is True
    assert prearm["workers"][SCHEDULER_WORKER]["seen_in_current_run"] is False


def test_stale_thresholds_match_worker_cadence_and_fail_closed():
    _factory, db = _database()
    now = datetime(2026, 8, 9, 12, 0, 0)
    opened_at = now - timedelta(minutes=5)
    touch_worker_heartbeat(
        db,
        SCHEDULER_WORKER,
        now=now - timedelta(seconds=91),
    )
    touch_worker_heartbeat(
        db,
        NOTIFICATION_WORKER,
        now=now - timedelta(seconds=46),
    )
    db.commit()

    snapshot = build_required_worker_liveness(
        db,
        current_run_opened_at=opened_at,
        now=now,
    )

    assert snapshot["healthy"] is False
    assert set(snapshot["blocking_codes"]) == {
        "scheduler_heartbeat_stale",
        "notification_worker_heartbeat_stale",
    }
    assert snapshot["workers"][SCHEDULER_WORKER]["stale_after_seconds"] == 90
    assert snapshot["workers"][NOTIFICATION_WORKER]["stale_after_seconds"] == 45


def test_production_operational_safety_requires_worker_liveness_by_default(monkeypatch):
    _factory, db = _database()
    now = datetime(2026, 8, 9, 12, 0, 0)
    opened_at = now - timedelta(seconds=30)
    monkeypatch.setenv("APP_ENV", "production")

    blocked = build_pilot_operational_safety(
        db,
        created_since=opened_at,
        now=now,
    )
    assert blocked["healthy"] is False
    assert blocked["worker_liveness"]["applicable"] is True
    assert set(blocked["blocking_codes"]) == {
        "scheduler_heartbeat_missing",
        "notification_worker_heartbeat_missing",
    }

    _seed_healthy(db, opened_at=opened_at, now=now)
    healthy = build_pilot_operational_safety(
        db,
        created_since=opened_at,
        now=now,
    )
    assert healthy["healthy"] is True
    assert healthy["blocking_codes"] == []
    assert healthy["worker_liveness"]["healthy"] is True


def test_nonproduction_queue_safety_does_not_require_worker_heartbeat(monkeypatch):
    _factory, db = _database()
    now = datetime(2026, 8, 9, 12, 0, 0)
    opened_at = now - timedelta(seconds=30)
    monkeypatch.setenv("APP_ENV", "development")

    snapshot = build_pilot_operational_safety(
        db,
        created_since=opened_at,
        now=now,
    )

    assert snapshot["healthy"] is True
    assert snapshot["worker_liveness"] == {
        "applicable": False,
        "healthy": True,
        "blocking_codes": [],
        "workers": {},
    }


def test_liveness_input_validation_is_fail_closed():
    _factory, db = _database()
    now = datetime(2026, 8, 9, 12, 0, 0)

    with pytest.raises(ValueError, match="scope cannot start in the future"):
        build_required_worker_liveness(
            db,
            current_run_opened_at=now + timedelta(seconds=1),
            now=now,
        )
    with pytest.raises(ValueError, match="Unsupported pilot worker"):
        touch_worker_heartbeat(db, "unknown-worker", now=now)
    with pytest.raises(ValueError, match="requires a runtime scope"):
        build_pilot_operational_safety(
            db,
            require_worker_liveness=True,
            now=now,
        )
