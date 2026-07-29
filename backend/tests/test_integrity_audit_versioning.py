import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_transaction_integrity.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_transaction_integrity", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_base_schema_does_not_require_future_tables():
    module = _load_module()

    checks = module._enabled_checks(set(module.BASE_REQUIRED_TABLES))

    assert "invalid_variant_inventory" in checks
    assert "negative_notification_delivery_attempts" not in checks
    assert "duplicate_webhook_destinations" not in checks


def test_notification_checks_enable_only_when_group_is_complete():
    module = _load_module()
    tables = set(module.BASE_REQUIRED_TABLES) | {"notification_delivery_states"}

    incomplete_checks = module._enabled_checks(tables)
    complete_checks = module._enabled_checks(tables | {"notifications"})

    assert "duplicate_notification_delivery_states" not in incomplete_checks
    assert "duplicate_notification_delivery_states" in complete_checks
    assert "orphan_notification_delivery_states" in complete_checks


def test_webhook_checks_enable_after_webhook_tables_exist():
    module = _load_module()
    tables = set(module.BASE_REQUIRED_TABLES) | {"webhook_destinations", "webhook_outbox"}

    checks = module._enabled_checks(tables)

    assert "invalid_webhook_destinations" in checks
    assert "negative_webhook_outbox_attempts" in checks
    assert "duplicate_webhook_destinations" in checks
