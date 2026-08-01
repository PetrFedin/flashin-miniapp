from pathlib import Path

from backend.notification_models import NotificationEventKey


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "backend" / "alembic" / "versions" / "0018_notification_event_keys.py"
PRODUCER = ROOT / "backend" / "services" / "notifications.py"


def test_event_key_model_has_durable_uniqueness_and_cascade():
    table = NotificationEventKey.__table__
    constraint_names = {
        constraint.name
        for constraint in table.constraints
        if constraint.name
    }

    assert "uq_notification_event_keys_event_key" in constraint_names
    assert "uq_notification_event_keys_notification" in constraint_names
    assert table.c.event_key.type.length == 255
    foreign_key = next(iter(table.c.notification_id.foreign_keys))
    assert foreign_key.ondelete == "CASCADE"


def test_migration_owns_notification_event_key_table():
    source = MIGRATION.read_text(encoding="utf-8")

    assert 'revision = "0018_notification_event_keys"' in source
    assert 'down_revision = "0017_promo_definition_constraints"' in source
    assert '"notification_event_keys"' in source
    assert 'name="uq_notification_event_keys_event_key"' in source
    assert 'ondelete="CASCADE"' in source
    assert 'timezone(\'UTC\', CURRENT_TIMESTAMP)' in source
    assert 'CURRENT_TIMESTAMP AT TIME ZONE' not in source


def test_order_notifications_use_business_event_keys_not_message_deduplication():
    source = PRODUCER.read_text(encoding="utf-8")

    assert 'event_key=f"order:{order.id}:paid"' in source
    assert 'f"order:{order.id}:status:{order.status}:delivery:{order.delivery_status}"' in source
    assert "Notification.message == message" not in source
    assert "NotificationEventKey.event_key == event_key" in source
