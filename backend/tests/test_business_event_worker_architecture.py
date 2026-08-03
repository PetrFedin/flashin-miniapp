from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DISPATCHER = ROOT / "backend" / "services" / "event_dispatcher.py"
SMOKE = ROOT / "scripts" / "business_event_worker_smoke.py"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
COMPOSE = ROOT / "docker-compose.yml"


def test_event_worker_claims_disjoint_rows_and_isolates_each_event():
    source = DISPATCHER.read_text(encoding="utf-8")

    assert ".with_for_update(skip_locked=True)" in source
    assert ".order_by(BusinessEvent.id.asc())" in source
    assert "with db.begin_nested():" in source
    assert "process_event(db, row)" in source
    assert "_record_failed_attempt(db, row, exc)" in source
    assert source.index("with db.begin_nested():") < source.index(
        "_record_failed_attempt(db, row, exc)"
    )
    assert "db.commit()" in source
    assert "_MAX_EVENT_ATTEMPTS = 10" in source
    assert "_MAX_BATCH_SIZE = 1000" in source


def test_event_worker_smoke_rolls_back_partial_outbox_and_exhausts_retries():
    source = SMOKE.read_text(encoding="utf-8")

    required_fragments = (
        'join_transaction_mode="create_savepoint"',
        "original_enqueue(db_session, event_type, payload)",
        "intentional poison event after outbox creation",
        "first_processed == 1",
        'healthy.status == "processed"',
        'poison.status == "pending"',
        "poison.attempts == 1",
        "len(first_outboxes) == 1",
        "range(2, 11)",
        'expected_status = "failed" if expected_attempt == 10 else "pending"',
        "WebhookOutbox.event_type == poison_type",
        "== 0",
        'poison.status == "failed"',
        "poison.attempts == 10",
        "len(outboxes) == 1",
        "outer_transaction.rollback()",
    )
    for fragment in required_fragments:
        assert fragment in source


def test_event_worker_smoke_rejects_invalid_batch_limits():
    source = SMOKE.read_text(encoding="utf-8")

    assert "for invalid_limit in (True, 0, -1, 1001):" in source
    assert "process_pending_events(db, limit=invalid_limit)" in source
    assert "except ValueError:" in source


def test_event_worker_is_a_separate_scalable_service():
    compose = COMPOSE.read_text(encoding="utf-8")

    assert "event_jobs:" in compose
    assert 'command: ["python", "scripts/run_event_jobs.py"]' in compose
    assert "- workers" in compose


def test_ci_runs_event_worker_smoke_before_full_backend_suite():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    refund_position = workflow.index("Run transactional cumulative refund smoke")
    event_position = workflow.index("Run atomic business event worker smoke")
    tests_position = workflow.index("Run backend tests")

    assert refund_position < event_position < tests_position
    assert "python scripts/business_event_worker_smoke.py" in workflow
