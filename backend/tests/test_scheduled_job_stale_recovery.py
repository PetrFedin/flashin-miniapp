from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base
from backend.job_models import ScheduledJobRun
from backend.jobs import execution


def test_next_locked_run_recovers_abandoned_running_audit(monkeypatch):
    sqlite_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(sqlite_engine)
    factory = sessionmaker(bind=sqlite_engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(execution, "engine", sqlite_engine)
    with execution._LOCAL_LOCKS_GUARD:
        execution._LOCAL_LOCKS.clear()

    db = factory()
    stale = ScheduledJobRun(
        job_name="events",
        run_token="a" * 32,
        trigger="scheduler",
        status="running",
        worker_id="dead-worker:1",
        started_at=datetime.utcnow() - timedelta(minutes=7),
        finished_at=None,
        duration_ms=None,
        result_json="{}",
        error="",
    )
    db.add(stale)
    db.commit()
    stale_id = stale.id
    db.close()

    outcome = execution.run_sync_job(
        "events",
        lambda _db: {"processed": 3},
        trigger="test",
        session_factory=factory,
    )

    assert outcome.status == "succeeded"
    db = factory()
    stale = db.query(ScheduledJobRun).filter(ScheduledJobRun.id == stale_id).one()
    current = db.query(ScheduledJobRun).filter(ScheduledJobRun.id == outcome.run_id).one()
    assert stale.status == "failed"
    assert stale.finished_at is not None
    assert stale.duration_ms >= 7 * 60 * 1000
    assert "Recovered stale scheduled job run" in stale.error
    assert current.status == "succeeded"
    db.close()
