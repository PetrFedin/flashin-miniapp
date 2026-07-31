import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base
from backend.job_models import ScheduledJobRun
from backend.jobs import execution, ops_jobs, scheduler_app


def _runtime(monkeypatch):
    sqlite_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(sqlite_engine)
    factory = sessionmaker(
        bind=sqlite_engine,
        autocommit=False,
        autoflush=False,
    )
    monkeypatch.setattr(execution, "engine", sqlite_engine)
    with execution._LOCAL_LOCKS_GUARD:
        execution._LOCAL_LOCKS.clear()
    return factory


def test_sync_job_records_success_and_result(monkeypatch):
    factory = _runtime(monkeypatch)

    outcome = execution.run_sync_job(
        "inventory-snapshot",
        lambda _db: {"snapshots": 17},
        trigger="test",
        session_factory=factory,
    )

    assert outcome.status == "succeeded"
    assert outcome.result == {"snapshots": 17}
    db = factory()
    run = db.query(ScheduledJobRun).one()
    assert run.job_name == "inventory-snapshot"
    assert run.status == "succeeded"
    assert run.finished_at is not None
    assert run.duration_ms is not None
    assert run.error == ""
    assert '"snapshots":17' in run.result_json
    db.close()


def test_failed_job_rolls_back_work_and_records_error(monkeypatch):
    factory = _runtime(monkeypatch)

    def fail(_db):
        raise RuntimeError("job exploded")

    with pytest.raises(RuntimeError, match="job exploded"):
        execution.run_sync_job(
            "events",
            fail,
            trigger="test",
            session_factory=factory,
        )

    db = factory()
    run = db.query(ScheduledJobRun).one()
    assert run.status == "failed"
    assert run.finished_at is not None
    assert run.duration_ms is not None
    assert run.error == "RuntimeError: job exploded"
    db.close()


def test_second_process_is_skipped_while_distributed_lock_is_held(monkeypatch):
    factory = _runtime(monkeypatch)
    executed = False

    def should_not_run(_db):
        nonlocal executed
        executed = True

    with execution.DistributedJobLock("outbox") as held:
        assert held.acquired is True
        outcome = execution.run_sync_job(
            "outbox",
            should_not_run,
            trigger="test",
            session_factory=factory,
        )

    assert executed is False
    assert outcome.status == "skipped"
    db = factory()
    run = db.query(ScheduledJobRun).one()
    assert run.status == "skipped"
    assert '"distributed_lock_unavailable"' in run.result_json
    db.close()


def test_async_job_uses_the_same_guard_and_audit_model(monkeypatch):
    factory = _runtime(monkeypatch)

    async def execute(_db):
        await asyncio.sleep(0)
        return 9

    outcome = asyncio.run(
        execution.run_async_job(
            "refund-reconciliation",
            execute,
            trigger="test",
            session_factory=factory,
        )
    )

    assert outcome.status == "succeeded"
    assert outcome.result == 9
    db = factory()
    assert db.query(ScheduledJobRun).one().status == "succeeded"
    db.close()


def test_lock_key_is_stable_and_job_specific():
    assert execution._lock_key("campaigns") == execution._lock_key("campaigns")
    assert execution._lock_key("campaigns") != execution._lock_key("events")


def test_result_serialization_is_bounded():
    rendered = execution._result_json({"payload": "x" * 50_000})

    assert len(rendered) <= 16_384
    assert '"truncated":true' in rendered


def test_orm_and_database_reject_incoherent_job_states(monkeypatch):
    factory = _runtime(monkeypatch)
    db = factory()
    db.add(
        ScheduledJobRun(
            job_name="events",
            run_token="a" * 32,
            trigger="test",
            status="failed",
            worker_id="worker:1",
            result_json="{}",
            error="",
            finished_at=None,
            duration_ms=None,
        )
    )
    with pytest.raises(HTTPException):
        db.commit()
    db.rollback()

    with pytest.raises(IntegrityError):
        db.execute(
            ScheduledJobRun.__table__.insert().values(
                job_name="events",
                run_token="b" * 32,
                trigger="test",
                status="succeeded",
                worker_id="worker:1",
                result_json="{}",
                error="",
                finished_at=None,
                duration_ms=None,
            )
        )
        db.commit()
    db.rollback()
    db.close()


def test_scheduler_registers_unique_guarded_jobs_in_utc(monkeypatch):
    monkeypatch.setattr(
        scheduler_app,
        "get_settings",
        lambda: SimpleNamespace(
            scheduler_enabled=True,
            moysklad_sync_interval_minutes=30,
        ),
    )

    scheduler = scheduler_app.build_scheduler()
    job_ids = {job.id for job in scheduler.get_jobs()}

    assert job_ids == {
        "campaigns",
        "events",
        "abandoned-carts",
        "inventory-snapshot",
        "sla",
        "outbox",
        "refund-reconciliation",
        "moysklad-sync",
    }
    assert scheduler._job_defaults["coalesce"] is True
    assert scheduler._job_defaults["max_instances"] == 1
    assert str(scheduler.timezone) == "UTC"


def test_abandoned_cart_job_uses_delivery_deduplication_and_row_locks():
    source = inspect.getsource(ops_jobs.queue_abandoned_cart_notifications)

    assert "queue_notification" in source
    assert "deduplication_key=" in source
    assert "with_for_update(skip_locked=True)" in source
    assert "Notification(" not in source


def test_worker_entrypoints_share_scheduler_lock_names():
    root = Path(__file__).resolve().parents[2]
    expected = {
        "run_campaign_jobs.py": '"campaigns"',
        "run_event_jobs.py": '"events"',
        "run_sla_jobs.py": '"sla"',
        "run_outbox_jobs.py": '"outbox"',
        "run_ops_jobs.py": '"abandoned-carts"',
        "run_media_jobs.py": '"media-jobs"',
    }
    for filename, lock_name in expected.items():
        source = (root / "scripts" / filename).read_text(encoding="utf-8")
        assert "run_sync_job" in source or "run_async_job" in source
        assert lock_name in source


def test_migration_creates_audited_job_runs_after_previous_head():
    source = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0032_scheduled_job_run_integrity.py"
    ).read_text(encoding="utf-8")

    assert "scheduled_job_runs" in source
    assert "ck_scheduled_job_runs_state_coherent" in source
    assert "ix_scheduled_job_runs_job_started" in source
    assert 'down_revision = "0031_media_asset_integrity"' in source
