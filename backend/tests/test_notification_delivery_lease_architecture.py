from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "bot" / "send_notifications.py"
SERVICE = ROOT / "backend" / "services" / "notification_delivery.py"
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
    service = SERVICE.read_text(encoding="utf-8")

    assert "def claim_pending_batch(" in service
    assert ".with_for_update(of=Notification, skip_locked=True)" in service
    assert "lease_token = uuid.uuid4().hex" in service
    assert "state.lease_token = lease_token" in service
    assert '"lease_token": lease_token' in service
    assert "limit < 1 or limit > 200" in service


def test_service_renews_and_finishes_only_current_owner():
    service = SERVICE.read_text(encoding="utf-8")

    assert "def renew_delivery_lease(" in service
    assert "def finish_delivery(" in service
    assert service.count("NotificationDeliveryState.lease_token == normalized_token") >= 2
    assert "state.lease_token = None" in service
    assert "db.delete(state)" in service
    assert "reset_notification_delivery" in service
    assert service.count("state.lease_token = None") >= 2


def test_bot_is_a_thin_transport_adapter_and_renews_before_send():
    worker = WORKER.read_text(encoding="utf-8")

    assert "from backend.services.notification_delivery import (" in worker
    assert "_claim_pending_batch_db = claim_pending_batch" in worker
    assert "_renew_delivery_lease_db = renew_delivery_lease" in worker
    assert "_finish_delivery_db = finish_delivery" in worker
    assert worker.count("_renew_delivery_lease(notification_id, lease_token)") >= 2
    assert "await bot.send_message(" in worker
    assert worker.index("_renew_delivery_lease(notification_id, lease_token)") < worker.index(
        "await bot.send_message("
    )


def test_stale_worker_smoke_uses_backend_service_and_transactional_postgres():
    source = SMOKE.read_text(encoding="utf-8")

    assert "from backend.services.notification_delivery import (" in source
    assert "from bot.send_notifications" not in source
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
