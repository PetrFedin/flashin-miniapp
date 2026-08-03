from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
JOB = ROOT / "backend" / "jobs" / "refund_jobs.py"
SMOKE = ROOT / "scripts" / "refund_reconciliation_review_smoke.py"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_refund_reviews_are_durable_and_excluded_from_automation():
    source = JOB.read_text(encoding="utf-8")
    pending_block = source.split("_PENDING_STATUSES =", 1)[1].split(
        "_FINAL_STATUSES",
        1,
    )[0]

    assert '"processing"' in pending_block
    assert '"refund_pending"' in pending_block
    assert '"refund_retry_required"' in pending_block
    assert '"refund_review_required"' not in pending_block
    assert "def _mark_refund_review_required(" in source
    assert 'ret.status = "refund_review_required"' in source
    assert 'order.status = "refund_requested"' in source
    assert 'order.payment_status = "refund_review_required"' in source
    assert "db.commit()" in source


def test_provider_validation_failures_persist_review_instead_of_rolling_back_only():
    source = JOB.read_text(encoding="utf-8")

    assert "provider_refund_amount(" in source
    assert "stored_amount = refund_money(" in source
    assert source.count("_mark_refund_review_required(db, return_id, order_id)") >= 3
    assert 'detail="Provider refund payload must be an object"' in source
    assert "except Exception:" in source


def test_review_smoke_uses_real_postgres_and_proves_no_automatic_recheck():
    source = SMOKE.read_text(encoding="utf-8")

    assert 'join_transaction_mode="create_savepoint"' in source
    assert "outer_transaction.rollback()" in source
    assert '"USD"' in source
    assert '"600.00"' in source
    assert 'persisted_currency_return.status == "refund_review_required"' in source
    assert 'persisted_amount_return.status == "refund_review_required"' in source
    assert 'persisted_valid_return.status == "approved_partial"' in source
    assert '"seen": 0' in source
    assert '"automatic_rechecks_after_review": 0' in source
    assert "sorted(provider_calls) == sorted(provider_payloads)" in source


def test_ci_runs_refund_review_smoke_before_full_backend_suite():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    scheduler_position = workflow.index("Run distributed scheduler lock smoke")
    refund_position = workflow.index("Run durable refund reconciliation review smoke")
    tests_position = workflow.index("Run backend tests")

    assert scheduler_position < refund_position < tests_position
    assert "python scripts/refund_reconciliation_review_smoke.py" in workflow
