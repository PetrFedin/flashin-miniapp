import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import BusinessEvent, Notification, WebhookOutbox
from backend.provider_models import ProviderCommand
from backend.services.pilot_operational_safety import build_pilot_operational_safety


def _database():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _insert_required(db, model, **overrides):
    """Insert a row without coupling this safety test to unrelated model fields."""
    values = dict(overrides)
    for column in model.__table__.columns:
        if column.name in values:
            continue
        if column.primary_key:
            continue
        if column.default is not None or column.server_default is not None or column.nullable:
            continue
        python_type = column.type.python_type
        if python_type is str:
            values[column.name] = f"secret-{model.__tablename__}-{column.name}"
        elif python_type is int:
            values[column.name] = 0
        elif python_type is float:
            values[column.name] = 0.0
        elif python_type is bool:
            values[column.name] = False
        elif python_type is datetime:
            values[column.name] = overrides.get("created_at") or datetime(2026, 8, 9, 12, 0, 0)
        else:  # pragma: no cover - fail loudly if a future required type is added
            raise AssertionError(
                f"Unsupported required column type for {model.__tablename__}.{column.name}"
            )
    db.execute(model.__table__.insert().values(**values))


def _insert_all_queues(db, *, status: str, created_at: datetime, next_attempt_at=None):
    db.add(
        ProviderCommand(
            provider="moysklad",
            command_type="customer_order.create",
            idempotency_key=f"secret-provider-{status}-{created_at.isoformat()}",
            aggregate_type="order",
            aggregate_id="secret-provider-order-id",
            payload_json='{"secret":"provider-payload"}',
            status=status if status != "processed" else "sent",
            next_attempt_at=next_attempt_at,
            last_error="secret-provider-error" if status == "failed" else "",
            created_at=created_at,
        )
    )
    _insert_required(
        db,
        BusinessEvent,
        status=status if status != "processing" else "pending",
        created_at=created_at,
    )
    _insert_required(
        db,
        WebhookOutbox,
        status=status if status != "processed" else "sent",
        created_at=created_at,
        next_attempt_at=next_attempt_at,
    )
    _insert_required(
        db,
        Notification,
        status=status if status != "processed" else "sent",
        created_at=created_at,
    )
    db.commit()


def test_fresh_pending_current_run_work_is_visible_but_non_blocking():
    db = _database()
    now = datetime(2026, 8, 9, 12, 0, 0)
    opened_at = now - timedelta(minutes=5)
    _insert_all_queues(
        db,
        status="pending",
        created_at=now - timedelta(minutes=1),
        next_attempt_at=now - timedelta(seconds=1),
    )

    snapshot = build_pilot_operational_safety(
        db,
        created_since=opened_at,
        now=now,
    )

    assert snapshot["healthy"] is True
    assert snapshot["blocking_codes"] == []
    assert snapshot["grace_minutes"] == 15
    assert snapshot["queues"]["moysklad_commands"]["counts"]["pending"] == 1
    assert snapshot["queues"]["business_events"]["counts"]["pending"] == 1
    assert snapshot["queues"]["webhook_outbox"]["counts"]["pending"] == 1
    assert snapshot["queues"]["telegram_notifications"]["counts"]["pending"] == 1


def test_historical_failures_before_runtime_window_are_ignored():
    db = _database()
    now = datetime(2026, 8, 9, 12, 0, 0)
    opened_at = now - timedelta(minutes=30)
    _insert_all_queues(
        db,
        status="failed",
        created_at=opened_at - timedelta(seconds=1),
        next_attempt_at=now - timedelta(minutes=20),
    )

    snapshot = build_pilot_operational_safety(
        db,
        created_since=opened_at,
        now=now,
    )

    assert snapshot["healthy"] is True
    assert snapshot["blocking_codes"] == []
    for queue in snapshot["queues"].values():
        assert sum(queue["counts"].values()) == 0


def test_current_run_terminal_failures_block_all_durable_delivery_spines_and_are_redacted():
    db = _database()
    now = datetime(2026, 8, 9, 12, 0, 0)
    opened_at = now - timedelta(minutes=30)
    _insert_all_queues(
        db,
        status="failed",
        created_at=now - timedelta(minutes=1),
        next_attempt_at=now - timedelta(seconds=1),
    )

    snapshot = build_pilot_operational_safety(
        db,
        created_since=opened_at,
        now=now,
    )

    assert snapshot["healthy"] is False
    assert set(snapshot["blocking_codes"]) == {
        "moysklad_command_terminal_failure",
        "business_event_terminal_failure",
        "webhook_outbox_terminal_failure",
        "notification_terminal_failure",
    }
    serialized = json.dumps(snapshot, ensure_ascii=False)
    assert "secret-" not in serialized
    assert "provider-payload" not in serialized
    assert "provider-error" not in serialized
    assert "provider-order-id" not in serialized


def test_overdue_pending_current_run_work_blocks_new_checkout_capacity():
    db = _database()
    now = datetime(2026, 8, 9, 12, 0, 0)
    opened_at = now - timedelta(minutes=30)
    _insert_all_queues(
        db,
        status="pending",
        created_at=now - timedelta(minutes=16),
        next_attempt_at=now - timedelta(seconds=1),
    )

    snapshot = build_pilot_operational_safety(
        db,
        created_since=opened_at,
        now=now,
    )

    assert snapshot["healthy"] is False
    assert set(snapshot["blocking_codes"]) == {
        "moysklad_command_overdue",
        "business_event_overdue",
        "webhook_outbox_overdue",
        "notification_overdue",
    }
    assert snapshot["queues"]["moysklad_commands"]["overdue_pending"] == 1
    assert snapshot["queues"]["business_events"]["overdue_pending"] == 1
    assert snapshot["queues"]["webhook_outbox"]["overdue_pending"] == 1
    assert snapshot["queues"]["telegram_notifications"]["overdue_pending"] == 1


def test_expired_processing_leases_block_provider_webhook_and_notification_delivery():
    db = _database()
    now = datetime(2026, 8, 9, 12, 0, 0)
    opened_at = now - timedelta(minutes=30)
    created_at = now - timedelta(minutes=1)

    db.add(
        ProviderCommand(
            provider="moysklad",
            command_type="customer_order.create",
            idempotency_key="secret-expired-provider-command",
            status="processing",
            next_attempt_at=now - timedelta(seconds=1),
            created_at=created_at,
        )
    )
    _insert_required(
        db,
        WebhookOutbox,
        status="processing",
        created_at=created_at,
        next_attempt_at=now - timedelta(seconds=1),
    )
    # A processing notification without a live delivery-state lease is itself
    # inconsistent and must fail closed; the outer join deliberately detects it.
    _insert_required(
        db,
        Notification,
        status="processing",
        created_at=created_at,
    )
    db.commit()

    snapshot = build_pilot_operational_safety(
        db,
        created_since=opened_at,
        now=now,
    )

    assert snapshot["healthy"] is False
    assert set(snapshot["blocking_codes"]) == {
        "moysklad_command_expired_lease",
        "webhook_outbox_expired_lease",
        "notification_expired_lease",
    }
    assert snapshot["queues"]["moysklad_commands"]["expired_processing"] == 1
    assert snapshot["queues"]["webhook_outbox"]["expired_processing"] == 1
    assert snapshot["queues"]["telegram_notifications"]["expired_processing"] == 1


def test_scope_and_grace_validation_fail_closed():
    db = _database()
    now = datetime(2026, 8, 9, 12, 0, 0)

    with pytest.raises(ValueError, match="scope cannot start in the future"):
        build_pilot_operational_safety(
            db,
            created_since=now + timedelta(seconds=1),
            now=now,
        )
    for invalid in (0, 121, True, 1.5):
        with pytest.raises(ValueError):
            build_pilot_operational_safety(db, grace_minutes=invalid, now=now)
