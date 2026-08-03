from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "backend" / "jobs" / "outbox_jobs.py"
MIGRATION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "0019_webhook_outbox_lease_tokens.py"
)
SMOKE = ROOT / "scripts" / "webhook_outbox_lease_smoke.py"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_migration_adds_indexed_nullable_lease_token():
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision = "0019_webhook_outbox_lease_tokens"' in source
    assert 'down_revision = "0018_notification_event_keys"' in source
    assert '"webhook_outbox"' in source
    assert 'sa.Column("lease_token", sa.String(length=64), nullable=True)' in source
    assert '"ix_webhook_outbox_lease_token"' in source
    assert 'op.drop_column("webhook_outbox", "lease_token")' in source


def test_claim_rotates_owner_token_with_skip_locked():
    source = WORKER.read_text(encoding="utf-8")

    assert "lease_token = uuid.uuid4().hex" in source
    assert "FOR UPDATE SKIP LOCKED" in source
    assert "lease_token = :lease_token" in source
    assert "target.lease_token" in source
    assert "ORDER BY id ASC" in source
    assert "_validate_batch_size(limit)" in source
    assert "limit < 1 or limit > 500" in source


def test_renew_and_finish_require_current_lease_owner():
    source = WORKER.read_text(encoding="utf-8")

    assert "def _renew_outbox_lease(" in source
    assert "def _finish_outbox(" in source
    assert source.count("AND lease_token = :lease_token") >= 4
    assert source.count("lease_token = NULL") >= 2
    assert source.count("_renew_outbox_lease(db, row_id, lease_token)") >= 2
    assert source.count("_finish_outbox(") >= 4
    assert '"X-Flashin-Event-Id": str(row_id)' in source
    assert '"X-Flashin-Event-Type": item["event_type"]' in source


def test_stale_worker_smoke_uses_real_transactional_postgres():
    source = SMOKE.read_text(encoding="utf-8")

    assert 'join_transaction_mode="create_savepoint"' in source
    assert "outer_transaction.rollback()" in source
    assert "first_token = first_claim[0][\"lease_token\"]" in source
    assert "second_token != first_token" in source
    assert "_renew_outbox_lease(db, row_id, first_token) is False" in source
    assert 'error="stale worker failure must be ignored"' in source
    assert "after_stale_worker[\"attempts\"] == 0" in source
    assert "after_stale_worker[\"lease_token\"] == second_token" in source
    assert "retry_state[\"attempts\"] == 1" in source
    assert "final_state[\"status\"] == \"sent\"" in source
    assert "final_state[\"lease_token\"] is None" in source
    assert "_claim_outbox(db, limit=1) == []" in source


def test_ci_runs_lease_smoke_after_event_atomicity_and_before_pytest():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    event_position = workflow.index("Run atomic business event worker smoke")
    lease_position = workflow.index("Run owned webhook outbox lease smoke")
    tests_position = workflow.index("Run backend tests")

    assert event_position < lease_position < tests_position
    assert "python scripts/webhook_outbox_lease_smoke.py" in workflow
