from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api import ops, ops_jobs
from backend.database import Base
from backend.job_models import ScheduledJobRun
from backend.jobs.execution import JobExecutionOutcome
from backend.jobs.registry import JOB_REGISTRY, JobDefinition
from backend.models import AdminUser, AuditLog


def _factory():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _admin(db, *, role="owner"):
    admin = AdminUser(
        email=f"{role}@example.com",
        password_hash="hash",
        role=role,
        active=True,
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    return admin


def _terminal_run(
    *,
    job_name="campaigns",
    status="failed",
    trigger="scheduler",
    result_json="{}",
    error="boom",
):
    now = datetime.utcnow()
    return ScheduledJobRun(
        job_name=job_name,
        run_token=("a" if status == "failed" else "b") * 32,
        trigger=trigger,
        status=status,
        worker_id="test:1",
        started_at=now,
        finished_at=now,
        duration_ms=1,
        result_json=result_json,
        error=error if status == "failed" else "",
    )


def test_ops_router_mounts_protected_job_routes():
    paths = {route.path for route in ops.router.routes}
    assert "/ops/jobs/definitions" in paths
    assert "/ops/jobs/runs" in paths
    assert "/ops/jobs/runs/{run_id}" in paths
    assert "/ops/jobs/{job_name}/run" in paths
    assert "/ops/jobs/runs/{run_id}/retry" in paths


def test_registry_is_fixed_unique_and_matches_worker_job_names():
    assert tuple(sorted(JOB_REGISTRY)) == tuple(item.name for item in ops_jobs.list_job_definitions())
    assert {
        "abandoned-carts",
        "campaigns",
        "events",
        "inventory-snapshot",
        "media-jobs",
        "moysklad-sync",
        "outbox",
        "refund-reconciliation",
        "sla",
    } == set(JOB_REGISTRY)
    assert all(item.permission for item in JOB_REGISTRY.values())
    assert all(item.kind in {"sync", "async"} for item in JOB_REGISTRY.values())


def test_unknown_job_cannot_be_executed():
    with pytest.raises(HTTPException) as exc_info:
        ops_jobs._require_definition("os.system")
    assert exc_info.value.status_code == 404


def test_definitions_require_security_read_and_expose_no_callable(monkeypatch):
    factory = _factory()
    db = factory()
    admin = _admin(db, role="manager")
    required = []
    monkeypatch.setattr(
        ops_jobs,
        "require_permission",
        lambda _db, _admin, permission: required.append(permission),
    )

    rows = ops_jobs.definitions(admin=admin, db=db)

    assert required == ["security.read"]
    assert rows
    assert all("function" not in row for row in rows)
    assert all(set(row) == {
        "name",
        "title",
        "description",
        "kind",
        "permission",
        "manual_enabled",
        "retry_enabled",
    } for row in rows)


def test_history_filters_and_paginates_deterministically(monkeypatch):
    factory = _factory()
    db = factory()
    admin = _admin(db)
    monkeypatch.setattr(ops_jobs, "require_permission", lambda *_args: None)
    first = _terminal_run(status="failed", trigger="scheduler")
    second = _terminal_run(status="skipped", trigger="manual", error="")
    second.run_token = "c" * 32
    third = _terminal_run(job_name="events", status="failed", trigger="worker")
    third.run_token = "d" * 32
    db.add_all([first, second, third])
    db.commit()

    page = ops_jobs.list_runs(
        job_name="campaigns",
        status=None,
        trigger=None,
        page=1,
        limit=1,
        admin=admin,
        db=db,
    )
    manual = ops_jobs.list_runs(
        job_name=None,
        status="skipped",
        trigger="manual",
        page=1,
        limit=25,
        admin=admin,
        db=db,
    )

    assert page["total"] == 2
    assert page["pages"] == 2
    assert len(page["items"]) == 1
    assert page["items"][0]["id"] == second.id
    assert manual["total"] == 1
    assert manual["items"][0]["id"] == second.id


@pytest.mark.parametrize(
    ("field", "value"),
    [("status", "unknown"), ("trigger", "cron-ish")],
)
def test_history_rejects_unknown_filters(monkeypatch, field, value):
    factory = _factory()
    db = factory()
    admin = _admin(db)
    monkeypatch.setattr(ops_jobs, "require_permission", lambda *_args: None)
    kwargs = {
        "job_name": None,
        "status": None,
        "trigger": None,
        "page": 1,
        "limit": 25,
        "admin": admin,
        "db": db,
    }
    kwargs[field] = value

    with pytest.raises(HTTPException) as exc_info:
        ops_jobs.list_runs(**kwargs)
    assert exc_info.value.status_code == 400


def test_run_detail_survives_malformed_result_json(monkeypatch):
    factory = _factory()
    db = factory()
    admin = _admin(db)
    monkeypatch.setattr(ops_jobs, "require_permission", lambda *_args: None)
    row = _terminal_run(result_json="{malformed")
    db.add(row)
    db.commit()

    payload = ops_jobs.get_run(row.id, admin=admin, db=db)

    assert payload["id"] == row.id
    assert payload["result"]["unavailable"] is True
    assert payload["result"]["preview"] == "{malformed"


def test_successful_manual_run_creates_run_and_admin_audit(monkeypatch):
    factory = _factory()
    request_db = factory()
    admin = _admin(request_db)
    calls = []

    def job(_db):
        calls.append("called")
        return {"processed": 3}

    definition = JobDefinition(
        name="test-success",
        title="Test",
        description="Test job",
        permission="security.read",
        kind="sync",
        function=job,
    )
    monkeypatch.setitem(JOB_REGISTRY, definition.name, definition)
    monkeypatch.setattr(ops_jobs, "JOB_SESSION_FACTORY", factory)

    payload = ops_jobs.run_registered_job(
        definition.name,
        admin=admin,
        db=request_db,
    )

    assert calls == ["called"]
    assert payload["status"] == "succeeded"
    assert payload["result"] == {"processed": 3}
    verify = factory()
    run = verify.query(ScheduledJobRun).filter_by(job_name=definition.name).one()
    audit = verify.query(AuditLog).filter_by(action="scheduled_job.executed").one()
    assert run.trigger == "manual"
    assert audit.entity_id == str(run.id)
    assert definition.name in audit.payload


def test_async_manual_run_uses_same_audit_contract(monkeypatch):
    factory = _factory()
    request_db = factory()
    admin = _admin(request_db)

    async def job(_db):
        return {"sent": 2}

    definition = JobDefinition(
        name="test-async",
        title="Async",
        description="Async test job",
        permission="security.read",
        kind="async",
        function=job,
    )
    monkeypatch.setitem(JOB_REGISTRY, definition.name, definition)
    monkeypatch.setattr(ops_jobs, "JOB_SESSION_FACTORY", factory)

    payload = ops_jobs.run_registered_job(
        definition.name,
        admin=admin,
        db=request_db,
    )

    assert payload["status"] == "succeeded"
    assert payload["result"] == {"sent": 2}


def test_failed_manual_run_returns_audited_run_id_without_internal_error(monkeypatch):
    factory = _factory()
    request_db = factory()
    admin = _admin(request_db)

    def job(_db):
        raise RuntimeError("secret provider credential leaked")

    definition = JobDefinition(
        name="test-failure",
        title="Failure",
        description="Failure test job",
        permission="security.read",
        kind="sync",
        function=job,
    )
    monkeypatch.setitem(JOB_REGISTRY, definition.name, definition)
    monkeypatch.setattr(ops_jobs, "JOB_SESSION_FACTORY", factory)

    with pytest.raises(HTTPException) as exc_info:
        ops_jobs.run_registered_job(
            definition.name,
            admin=admin,
            db=request_db,
        )

    assert exc_info.value.status_code == 500
    detail = exc_info.value.detail
    assert detail["message"] == "Scheduled job failed"
    assert isinstance(detail["run_id"], int)
    assert "credential" not in str(detail)
    verify = factory()
    run = verify.query(ScheduledJobRun).filter_by(id=detail["run_id"]).one()
    assert run.status == "failed"
    assert "credential" in run.error
    audit = verify.query(AuditLog).filter_by(action="scheduled_job.failed").one()
    assert audit.entity_id == str(run.id)


def test_skipped_manual_run_returns_conflict_and_run_id(monkeypatch):
    factory = _factory()
    db = factory()
    admin = _admin(db)
    now = datetime.utcnow()
    skipped = ScheduledJobRun(
        job_name="campaigns",
        run_token="e" * 32,
        trigger="manual",
        status="skipped",
        worker_id="test:1",
        started_at=now,
        finished_at=now,
        duration_ms=0,
        result_json='{"reason":"distributed_lock_unavailable"}',
        error="",
    )
    db.add(skipped)
    db.commit()
    monkeypatch.setattr(
        ops_jobs,
        "_execute_definition",
        lambda _definition: JobExecutionOutcome(
            job_name="campaigns",
            status="skipped",
            run_id=skipped.id,
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        ops_jobs.run_registered_job("campaigns", admin=admin, db=db)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["run_id"] == skipped.id


def test_retry_only_accepts_failed_or_skipped_registered_runs(monkeypatch):
    factory = _factory()
    db = factory()
    admin = _admin(db)
    succeeded = _terminal_run(status="succeeded", error="")
    db.add(succeeded)
    db.commit()

    with pytest.raises(HTTPException) as exc_info:
        ops_jobs.retry_registered_job(succeeded.id, admin=admin, db=db)

    assert exc_info.value.status_code == 409
    assert "failed or skipped" in exc_info.value.detail


def test_retry_executes_same_allowlisted_job_and_records_source(monkeypatch):
    factory = _factory()
    db = factory()
    admin = _admin(db)
    source = _terminal_run(job_name="test-retry", status="failed")
    source.run_token = "f" * 32
    db.add(source)
    db.commit()
    calls = []

    def job(_db):
        calls.append("retried")
        return 7

    definition = JobDefinition(
        name="test-retry",
        title="Retry",
        description="Retry test job",
        permission="security.read",
        kind="sync",
        function=job,
    )
    monkeypatch.setitem(JOB_REGISTRY, definition.name, definition)
    monkeypatch.setattr(ops_jobs, "JOB_SESSION_FACTORY", factory)

    payload = ops_jobs.retry_registered_job(source.id, admin=admin, db=db)

    assert calls == ["retried"]
    assert payload["status"] == "succeeded"
    verify = factory()
    audit = verify.query(AuditLog).filter_by(action="scheduled_job.executed").one()
    assert f'"source_run_id": {source.id}' in audit.payload


def test_permission_is_checked_before_request_transaction_is_released(monkeypatch):
    factory = _factory()
    db = factory()
    admin = _admin(db, role="support")
    executed = []
    monkeypatch.setattr(
        ops_jobs,
        "_execute_definition",
        lambda _definition: executed.append(True),
    )

    with pytest.raises(HTTPException) as exc_info:
        ops_jobs.run_registered_job("inventory-snapshot", admin=admin, db=db)

    assert exc_info.value.status_code == 403
    assert executed == []
