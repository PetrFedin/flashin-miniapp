from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import WebhookDestination, WebhookOutbox
from backend.webhook_statuses import MAX_WEBHOOK_BODY_BYTES


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_direct_sql_rejects_oversized_outbox_payload():
    db = _session()
    oversized_payload = '{"blob":"' + ("x" * MAX_WEBHOOK_BODY_BYTES) + '"}'

    with pytest.raises(IntegrityError):
        db.execute(
            WebhookOutbox.__table__.insert().values(
                destination="https://hooks.example.com/events",
                event_type="order.paid",
                payload=oversized_payload,
                status="pending",
                attempts=0,
                last_error="",
                next_attempt_at=datetime.utcnow(),
            )
        )
        db.commit()
    db.rollback()


def test_direct_sql_rejects_oversized_outbox_error():
    db = _session()

    with pytest.raises(IntegrityError):
        db.execute(
            WebhookOutbox.__table__.insert().values(
                destination="invalid-destination",
                event_type="order.paid",
                payload="{}",
                status="failed",
                attempts=10,
                last_error="x" * 2001,
                next_attempt_at=None,
            )
        )
        db.commit()
    db.rollback()


@pytest.mark.parametrize(
    "values",
    [
        {
            "url": "https://hooks.example.com/" + ("x" * 240),
            "event_type": "order.paid",
        },
        {
            "url": "https://hooks.example.com/events",
            "event_type": "x" * 121,
        },
    ],
)
def test_direct_sql_rejects_oversized_destination_identity(values):
    db = _session()

    with pytest.raises(IntegrityError):
        db.execute(
            WebhookDestination.__table__.insert().values(
                name="Webhook",
                active=True,
                signing_secret="",
                **values,
            )
        )
        db.commit()
    db.rollback()


def test_payload_limit_migration_discards_oversized_legacy_rows_before_constraints():
    source = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0027_webhook_payload_limits.py"
    ).read_text(encoding="utf-8")

    repair_position = source.index("UPDATE webhook_outbox")
    constraint_position = source.index("op.create_check_constraint")

    assert repair_position < constraint_position
    assert "Discarded oversized legacy webhook payload" in source
    assert "ck_webhook_outbox_payload_size" in source
    assert "ck_webhook_outbox_error_size" in source
