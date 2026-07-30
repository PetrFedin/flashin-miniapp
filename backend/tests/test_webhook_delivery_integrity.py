from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import WebhookDestination, WebhookOutbox
from backend.services.outbox import schedule_retry
from backend.webhook_statuses import MAX_WEBHOOK_ATTEMPTS, WEBHOOK_OUTBOX_STATUSES


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _pending(**overrides):
    values = {
        "destination": "https://hooks.example.com/events",
        "event_type": "order.paid",
        "payload": '{"order_id":1}',
        "status": "pending",
        "attempts": 0,
        "last_error": "",
        "next_attempt_at": datetime.utcnow(),
    }
    values.update(overrides)
    return WebhookOutbox(**values)


def test_destination_and_outbox_are_normalized_before_insert():
    db = _session()
    destination = WebhookDestination(
        name="  Primary hook  ",
        url="HTTPS://Hooks.Example.com:443/events",
        event_type=" ORDER.PAID ",
        active=True,
        signing_secret="x" * 32,
    )
    row = _pending(
        destination="HTTPS://Hooks.Example.com:443/events",
        event_type=" ORDER.PAID ",
        payload=' { "z": 2, "order_id": 1 } ',
    )
    db.add_all([destination, row])
    db.commit()
    db.refresh(destination)
    db.refresh(row)

    assert destination.name == "Primary hook"
    assert destination.url == "https://hooks.example.com/events"
    assert destination.event_type == "order.paid"
    assert row.destination == "https://hooks.example.com/events"
    assert row.event_type == "order.paid"
    assert row.payload == '{"order_id":1,"z":2}'
    assert json.loads(row.payload)["z"] == 2


@pytest.mark.parametrize(
    "values",
    [
        {"name": "", "url": "https://hooks.example.com/events"},
        {"name": "Hook", "url": "internal://order-paid"},
        {"name": "Hook", "url": "https://localhost/events"},
        {"name": "Hook", "url": "https://hooks.example.com/events#debug"},
        {"name": "Hook", "url": "https://hooks.example.com/events", "event_type": "bad event"},
        {"name": "Hook", "url": "https://hooks.example.com/events", "signing_secret": "short"},
    ],
)
def test_invalid_destination_is_rejected_before_write(values):
    db = _session()
    payload = {
        "name": "Hook",
        "url": "https://hooks.example.com/events",
        "event_type": "*",
        "active": True,
        "signing_secret": "",
    }
    payload.update(values)
    db.add(WebhookDestination(**payload))

    with pytest.raises(HTTPException) as caught:
        db.flush()
    assert caught.value.status_code == 400
    db.rollback()


def test_duplicate_destination_after_normalization_is_rejected():
    db = _session()
    db.add(
        WebhookDestination(
            name="First",
            url="https://hooks.example.com/events",
            event_type="order.paid",
            active=True,
            signing_secret="",
        )
    )
    db.commit()
    db.add(
        WebhookDestination(
            name="Second",
            url="HTTPS://HOOKS.EXAMPLE.COM:443/events",
            event_type=" ORDER.PAID ",
            active=True,
            signing_secret="",
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    assert db.query(WebhookDestination).count() == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"destination": ""},
        {"event_type": "bad event"},
        {"payload": ""},
        {"payload": "not-json"},
        {"payload": "null"},
        {"status": "unknown"},
        {"attempts": -1},
        {"attempts": 11},
        {"status": "pending", "attempts": 10},
        {"status": "pending", "next_attempt_at": None},
        {"status": "sent", "next_attempt_at": datetime.utcnow()},
        {"status": "failed", "attempts": 9, "last_error": "failed", "next_attempt_at": None},
        {"status": "failed", "attempts": 10, "last_error": "", "next_attempt_at": None},
        {"status": "discarded", "last_error": "", "next_attempt_at": None},
    ],
)
def test_invalid_outbox_state_is_rejected_before_write(overrides):
    db = _session()
    row = _pending(**overrides)
    db.add(row)

    with pytest.raises(HTTPException) as caught:
        db.flush()
    assert caught.value.status_code == 400
    db.rollback()


def test_failed_invalid_destination_is_preserved_for_audit():
    db = _session()
    row = _pending(
        destination="not-a-valid-url",
        status="failed",
        attempts=MAX_WEBHOOK_ATTEMPTS,
        last_error="Webhook URL must use http or https",
        next_attempt_at=None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    assert row.destination == "not-a-valid-url"
    assert row.status == "failed"
    assert row.attempts == MAX_WEBHOOK_ATTEMPTS
    assert row.next_attempt_at is None


def test_retry_scheduler_produces_database_valid_terminal_state():
    db = _session()
    row = _pending(attempts=9)
    db.add(row)
    db.commit()

    schedule_retry(row, "provider unavailable")
    db.commit()
    db.refresh(row)

    assert row.status == "failed"
    assert row.attempts == MAX_WEBHOOK_ATTEMPTS
    assert row.last_error == "provider unavailable"
    assert row.next_attempt_at is None


@pytest.mark.parametrize(
    "values",
    [
        {"destination": ""},
        {"event_type": " ORDER.PAID "},
        {"payload": ""},
        {"status": "unknown"},
        {"attempts": -1},
        {"attempts": 11},
        {"status": "pending", "attempts": 10},
        {"status": "pending", "next_attempt_at": None},
        {"status": "sent", "next_attempt_at": datetime.utcnow()},
        {"status": "failed", "attempts": 9, "last_error": "failed", "next_attempt_at": None},
        {"status": "discarded", "last_error": "", "next_attempt_at": None},
    ],
)
def test_direct_sql_cannot_bypass_outbox_state_constraints(values):
    db = _session()
    payload = {
        "destination": "https://hooks.example.com/events",
        "event_type": "order.paid",
        "payload": "{}",
        "status": "pending",
        "attempts": 0,
        "last_error": "",
        "next_attempt_at": datetime.utcnow(),
    }
    payload.update(values)

    with pytest.raises(IntegrityError):
        db.execute(WebhookOutbox.__table__.insert().values(**payload))
        db.commit()
    db.rollback()


def test_status_catalog_and_metadata_constraints_are_complete():
    assert WEBHOOK_OUTBOX_STATUSES == {"pending", "sent", "failed", "discarded"}
    destination_constraints = {
        constraint.name for constraint in WebhookDestination.__table__.constraints
    }
    outbox_constraints = {constraint.name for constraint in WebhookOutbox.__table__.constraints}

    assert {
        "ck_webhook_destinations_name_nonempty",
        "ck_webhook_destinations_name_normalized",
        "ck_webhook_destinations_url_normalized",
        "ck_webhook_destinations_event_type_normalized",
        "ck_webhook_destinations_secret_length",
        "uq_webhook_destinations_url_event_type",
    }.issubset(destination_constraints)
    assert {
        "ck_webhook_outbox_attempts_nonnegative",
        "ck_webhook_outbox_destination_nonempty",
        "ck_webhook_outbox_event_type_normalized",
        "ck_webhook_outbox_payload_nonempty",
        "ck_webhook_outbox_status_valid",
        "ck_webhook_outbox_attempts_bounded",
        "ck_webhook_outbox_pending_schedule",
        "ck_webhook_outbox_terminal_schedule_empty",
        "ck_webhook_outbox_failed_state",
        "ck_webhook_outbox_discarded_reason",
    }.issubset(outbox_constraints)


def test_webhook_migration_repairs_legacy_rows_before_constraints():
    source = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0026_webhook_delivery_integrity.py"
    ).read_text(encoding="utf-8")

    json_function_position = source.index("FUNCTION pg_temp.is_json_collection")
    destination_map_position = source.index("webhook_destination_normalization_map")
    destination_temp_position = source.index("invalid.invalid/migration")
    outbox_map_position = source.index("webhook_outbox_normalization_map")
    repair_position = source.index("UPDATE webhook_outbox AS row")
    constraint_position = source.index("op.create_check_constraint")

    assert json_function_position < destination_map_position < destination_temp_position
    assert destination_temp_position < outbox_map_position < repair_position < constraint_position
    assert "Discarded invalid legacy webhook row" in source
    assert "Webhook attempt limit reached" in source
    assert "duplicate_rank = 1" in source
