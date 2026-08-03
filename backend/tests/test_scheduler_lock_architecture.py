from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LOCK_SERVICE = ROOT / "backend" / "jobs" / "scheduler_lock.py"
SCHEDULER = ROOT / "backend" / "jobs" / "scheduler_app.py"
MOYSKLAD_JOB = ROOT / "backend" / "jobs" / "moysklad_jobs.py"
SMOKE = ROOT / "scripts" / "scheduler_lock_smoke.py"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


ENTRYPOINTS = {
    "scripts/run_campaign_jobs.py": 'run_locked_db_job("campaigns"',
    "scripts/run_event_jobs.py": 'run_locked_db_job("events"',
    "scripts/run_sla_jobs.py": 'run_locked_db_job("sla"',
    "scripts/run_outbox_jobs.py": 'run_locked_async_db_job("outbox"',
    "scripts/run_moysklad_sync.py": "main()",
}


def test_scheduler_lock_uses_session_advisory_lock_on_dedicated_connection():
    source = LOCK_SERVICE.read_text(encoding="utf-8")

    assert "with database_engine.connect() as connection:" in source
    assert "SELECT pg_try_advisory_lock(:lock_key)" in source
    assert "SELECT pg_advisory_unlock(:lock_key)" in source
    assert source.index("pg_try_advisory_lock") < source.index("callback()")
    assert source.index("callback()") < source.index("pg_advisory_unlock")
    assert "connection.commit()" in source
    assert '"status": "skipped"' in source
    assert '"reason": "lock_busy"' in source
    assert "connection.invalidate()" in source


def test_scheduler_lock_key_is_deterministic_signed_and_namespaced():
    source = LOCK_SERVICE.read_text(encoding="utf-8")

    assert '_LOCK_NAMESPACE = "flashin:scheduler"' in source
    assert "hashlib.sha256(" in source
    assert 'byteorder="big", signed=True' in source
    assert "_JOB_NAME_RE.fullmatch(normalized)" in source
    assert "_JOB_NAME_MAX_LENGTH = 120" in source


def test_scheduler_wraps_every_registered_job_with_its_own_lock_id():
    source = SCHEDULER.read_text(encoding="utf-8")

    db_jobs = (
        "campaigns",
        "events",
        "abandoned-carts",
        "inventory-snapshot",
        "sla",
    )
    async_jobs = (
        "outbox",
        "refund-reconciliation",
        "moysklad-sync",
    )
    for job_name in db_jobs:
        assert f'_run_db_job("{job_name}"' in source or (
            f'"{job_name}",' in source and "_run_db_job(" in source
        )
    for job_name in async_jobs:
        assert f'_run_async_db_job("{job_name}"' in source or (
            f'"{job_name}",' in source and "_run_async_db_job(" in source
        )
    assert source.count("scheduler.add_job(") == 8
    assert '"max_instances": 1' in source


def test_one_shot_entrypoints_share_scheduler_lock_ids():
    for relative_path, fragment in ENTRYPOINTS.items():
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert fragment in source

    ops = (ROOT / "scripts" / "run_ops_jobs.py").read_text(encoding="utf-8")
    assert '"abandoned-carts"' in ops
    assert '"inventory-snapshot"' in ops

    moysklad = MOYSKLAD_JOB.read_text(encoding="utf-8")
    assert 'run_locked_async_db_job(' in moysklad
    assert '"moysklad-sync"' in moysklad


def test_scheduler_lock_smoke_proves_contention_independence_and_release():
    source = SMOKE.read_text(encoding="utf-8")

    assert "duplicate_execution" in source
    assert '"reason": "lock_busy"' in source
    assert "independent_execution" in source
    assert "reacquired_after_release" in source
    assert "reacquired_after_exception" in source
    assert "raise RuntimeError(\"scheduler smoke failure\")" in source
    assert "same_key == advisory_lock_key(same_job)" in source
    assert "same_key != advisory_lock_key(other_job)" in source


def test_ci_runs_scheduler_lock_smoke_before_full_backend_suite():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    notification_position = workflow.index(
        "Run owned notification delivery lease smoke"
    )
    scheduler_position = workflow.index("Run distributed scheduler lock smoke")
    tests_position = workflow.index("Run backend tests")

    assert notification_position < scheduler_position < tests_position
    assert "python scripts/scheduler_lock_smoke.py" in workflow
