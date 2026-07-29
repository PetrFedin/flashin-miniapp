import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_transaction_integrity.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_transaction_integrity", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_base_schema_does_not_require_future_revisions():
    module = _load_module()

    checks = module._enabled_checks(set(module.BASE_REQUIRED_TABLES), set())

    assert "invalid_variant_inventory" in checks
    assert "negative_notification_delivery_attempts" not in checks
    assert "duplicate_webhook_destinations" not in checks


def test_notification_checks_require_table_group_and_revision():
    module = _load_module()
    tables = set(module.BASE_REQUIRED_TABLES) | {
        "notification_delivery_states",
        "notifications",
    }

    before_revision = module._enabled_checks(tables, set())
    after_revision = module._enabled_checks(
        tables,
        {"0012_notification_delivery_retry_state"},
    )

    assert "duplicate_notification_delivery_states" not in before_revision
    assert "duplicate_notification_delivery_states" in after_revision
    assert "orphan_notification_delivery_states" in after_revision


def test_webhook_checks_do_not_block_cleanup_migration():
    module = _load_module()
    tables = set(module.BASE_REQUIRED_TABLES) | {
        "webhook_destinations",
        "webhook_outbox",
    }

    before_revision = module._enabled_checks(
        tables,
        {"0012_notification_delivery_retry_state"},
    )
    after_revision = module._enabled_checks(
        tables,
        {
            "0012_notification_delivery_retry_state",
            "0013_webhook_outbox_integrity",
        },
    )

    assert "duplicate_webhook_destinations" not in before_revision
    assert "duplicate_webhook_destinations" in after_revision
    assert "negative_webhook_outbox_attempts" in after_revision
