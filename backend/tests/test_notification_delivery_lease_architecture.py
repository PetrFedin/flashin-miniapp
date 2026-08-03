from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "bot" / "send_notifications.py"
MODEL = ROOT / "backend" / "notification_models.py"
MIGRATION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "0020_notification_delivery_lease_tokens.py"
)
SMOKE = ROOT / "scripts" / "notification_delivery_lease_smoke.py"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


def test_notification_delivery_state_maps_indexed_lease_token():
    model = MODEL.read_text(encoding="utf-8")
    migration = MIGRATION.read_text(encoding="utf-8")

    assert "lease_token: Mapped[str | None]" in model
    assert "mapped_column(String(64), nullable=True, index=True)" in model
    assert 'revision = "0020_notification_delivery_lease_tokens"' in migration
    assert 'down_revision = "0019_webhook_outbox_lease_tokens"' in migration
    assert '"notification_delivery_states"' in migration
    assert 'sa.Column("lease_token", sa.String(length=64), nullable=True)' in migration
    assert '"ix_notification_delivery_states_lease_token"' in migration


def test_claim_rotates_a_unique_token_per_notification():
    source = WORKER.read_text(encoding="utf-8")

    assert "def _claim_pending_batch_db(" in source
    assert ".with_for_update(of=Notification, skip_locked=True)" in source
    assert "lease_token = uuid.uuid4().hex" in source
    assert "state.lease_token = lease_token" in source
    assert '"lease_token": lease_token' in source
    assert "limit < 1 or limit > 200" in source


def test_send_path_renews_and_finishes_only_current_owner():
    source = WORKER.read_text(encoding="utf-8")

    assert "def _renew_delivery_lease_db(" in source
    assert "def _finish_delivery_db(" in source
    assert source.count("NotificationDeliveryState.lease_token == normalized_token") >= 2
    assert source.count("_renew_delivery_lease(notification_id, lease_token)") >= 2
    assert "state.lease_token = None" in source
    assert "await bot.send_message(" in source
    assert source.index("_renew_delivery_lease(notification_id, lease_token)") < source.index(
        "await bot.send_message("
    )


def test_stale_worker_smoke_uses_real_transactional_postgres():
    source = SMOKE.read_text(encoding="utf-8")

    assert 'join_transaction_mode="create_savepoint"' in source
    assert "outer_transaction.rollback()" in source
    assert "second_token != first_token" in source
    assert "_renew_delivery_lease_db(db, notification_id, first_token) is False" in source
    assert 'RuntimeError("stale worker failure must be ignored")' in source
    assert '== "ignored"' in source
    assert "after_stale_state.attempts == 0" in source
    assert "after_stale_state.lease_token == second_token" in source
    assert '== "retry_scheduled"' in source
    assert "retry_state.lease_token is None" in source
    assert '== "sent"' in source
    assert "final_state is None" in source
    assert "_claim_pending_batch_db(db, limit=1) == []" in source


def test_ci_runs_notification_lease_smoke_before_full_backend_suite():
    workflow = WORKFLOW.read_text(encoding="utf-8")

    outbox_position = workflow.index("Run owned webhook outbox lease smoke")
    notification_position = workflow.index(
        "Run owned notification delivery lease smoke"
    )
    tests_position = workflow.index("Run backend tests")

    assert outbox_position < notification_position < tests_position
    assert "python scripts/notification_delivery_lease_smoke.py" in workflow
