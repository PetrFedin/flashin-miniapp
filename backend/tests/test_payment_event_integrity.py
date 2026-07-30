from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import PaymentEvent
from backend.payment_statuses import (
    ACTIONABLE_PAYMENT_EVENT_TYPES,
    PERSISTED_PAYMENT_EVENT_TYPES,
    UNRESOLVED_PAYMENT_EVENT,
)


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _event(**overrides):
    values = {
        "provider": "yookassa",
        "provider_payment_id": "payment-1",
        "event_type": "payment.succeeded",
        "raw_payload": '{"event":"payment.succeeded","value":1}',
        "processed": False,
    }
    values.update(overrides)
    return PaymentEvent(**values)


def test_orm_normalizes_payment_event_and_canonicalizes_json():
    db = _session()
    event = _event(
        provider=" YooKassa ",
        provider_payment_id=" payment-1 ",
        event_type=" PAYMENT.SUCCEEDED ",
        raw_payload=' { "value": 1, "event": "payment.succeeded" } ',
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    assert event.provider == "yookassa"
    assert event.provider_payment_id == "payment-1"
    assert event.event_type == "payment.succeeded"
    assert event.raw_payload == '{"event":"payment.succeeded","value":1}'
    assert json.loads(event.raw_payload)["value"] == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider": ""},
        {"provider": "legacy_unresolved"},
        {"provider_payment_id": ""},
        {"event_type": "payment.unresolved"},
        {"event_type": "payment.unknown"},
        {"raw_payload": ""},
        {"raw_payload": "not-json"},
        {"raw_payload": "[]"},
        {"raw_payload": "null"},
    ],
)
def test_orm_rejects_invalid_new_payment_events(overrides):
    db = _session()
    db.add(_event(**overrides))

    with pytest.raises(HTTPException) as caught:
        db.flush()
    assert caught.value.status_code == 400
    db.rollback()
    assert db.query(PaymentEvent).count() == 0


@pytest.mark.parametrize(
    "values",
    [
        {"provider": " YooKassa "},
        {"provider_payment_id": " payment-1 "},
        {"event_type": "PAYMENT.SUCCEEDED"},
        {"event_type": "payment.unknown"},
        {"raw_payload": ""},
        {
            "provider": "legacy_unresolved",
            "event_type": "payment.succeeded",
            "processed": False,
        },
        {
            "provider": "yookassa",
            "event_type": "payment.unresolved",
            "processed": False,
        },
        {
            "provider": "legacy_unresolved",
            "provider_payment_id": "legacy-event-1",
            "event_type": "payment.unresolved",
            "processed": True,
        },
    ],
)
def test_direct_sql_cannot_bypass_payment_event_constraints(values):
    db = _session()
    payload = {
        "provider": "yookassa",
        "provider_payment_id": "payment-1",
        "event_type": "payment.succeeded",
        "raw_payload": "{}",
        "processed": False,
    }
    payload.update(values)

    with pytest.raises(IntegrityError):
        db.execute(PaymentEvent.__table__.insert().values(**payload))
        db.commit()
    db.rollback()


def test_legacy_unresolved_event_is_allowed_only_in_quarantined_state():
    db = _session()
    db.execute(
        PaymentEvent.__table__.insert().values(
            provider="legacy_unresolved",
            provider_payment_id="legacy-event-1",
            event_type=UNRESOLVED_PAYMENT_EVENT,
            raw_payload='{"legacy_event_id":1}',
            processed=False,
        )
    )
    db.commit()

    event = db.query(PaymentEvent).one()
    assert event.provider == "legacy_unresolved"
    assert event.event_type == UNRESOLVED_PAYMENT_EVENT
    assert event.processed is False


def test_duplicate_provider_payment_event_is_idempotently_rejected():
    db = _session()
    db.add(_event())
    db.commit()
    db.add(_event(raw_payload='{"event":"payment.succeeded","retry":true}'))

    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    assert db.query(PaymentEvent).count() == 1


def test_payment_event_catalogs_are_exact():
    assert ACTIONABLE_PAYMENT_EVENT_TYPES == {
        "payment.waiting_for_capture",
        "payment.succeeded",
        "payment.canceled",
    }
    assert PERSISTED_PAYMENT_EVENT_TYPES == ACTIONABLE_PAYMENT_EVENT_TYPES | {
        "payment.unresolved"
    }


def test_payment_event_metadata_contains_integrity_constraints():
    names = {constraint.name for constraint in PaymentEvent.__table__.constraints}
    assert {
        "ck_payment_events_provider_normalized",
        "ck_payment_events_provider_payment_id_normalized",
        "ck_payment_events_event_type_valid",
        "ck_payment_events_event_type_normalized",
        "ck_payment_events_payload_nonempty",
        "ck_payment_events_legacy_state_coherent",
    }.issubset(names)
    index_names = {index.name for index in PaymentEvent.__table__.indexes}
    assert "uq_payment_events_provider_event" in index_names


def test_payment_event_migration_quarantines_invalid_json_and_duplicates():
    source = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0025_payment_event_integrity.py"
    ).read_text(encoding="utf-8")

    function_position = source.index("FUNCTION pg_temp.is_json_object")
    mapping_position = source.index("CREATE TEMP TABLE payment_event_normalization_map")
    temporary_position = source.index("provider = 'migration_tmp'")
    quarantine_position = source.index("candidate_provider := 'legacy_unresolved'")
    restore_position = source.index("SET provider = mapping.final_provider")
    constraint_position = source.index("op.create_check_constraint")

    assert function_position < mapping_position < temporary_position
    assert temporary_position < quarantine_position < restore_position < constraint_position
    assert "payload_is_object" in source
    assert "duplicate_rank = 1" in source
    assert "jsonb_build_object" in source
