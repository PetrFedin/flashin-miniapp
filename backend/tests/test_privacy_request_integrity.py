import inspect
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend.api import privacy as privacy_api
from backend.database import Base
from backend.models import ConsentRecord, Customer, PrivacyRequest
from backend.services import privacy


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _customer(db, telegram_id: str = "123456") -> Customer:
    customer = Customer(telegram_id=telegram_id)
    db.add(customer)
    db.commit()
    return customer


def test_only_one_open_request_per_customer_and_type():
    db = _session()
    customer = _customer(db)
    db.add_all(
        [
            PrivacyRequest(
                customer_id=customer.id,
                request_type="export",
                status="requested",
                result_url="",
                processed_at=None,
            ),
            PrivacyRequest(
                customer_id=customer.id,
                request_type="export",
                status="processing",
                result_url="",
                processed_at=None,
            ),
        ]
    )

    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_terminal_request_state_requires_processed_at():
    db = _session()
    customer = _customer(db)
    db.execute(
        PrivacyRequest.__table__.insert().values(
            customer_id=customer.id,
            request_type="delete",
            status="processed",
            result_url="",
            processed_at=None,
        )
    )

    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_orm_normalizes_request_and_consent_values():
    db = _session()
    customer = _customer(db)
    request = PrivacyRequest(
        customer_id=customer.id,
        request_type=" EXPORT ",
        status=" REQUESTED ",
        result_url="",
        processed_at=None,
    )
    consent = ConsentRecord(
        customer_id=customer.id,
        consent_type=" MARKETING ",
        granted=True,
        source=" Telegram_Mini_App ",
    )
    db.add_all([request, consent])
    db.commit()

    assert request.request_type == "export"
    assert request.status == "requested"
    assert consent.consent_type == "marketing"
    assert consent.source == "telegram_mini_app"


def test_orm_rejects_unknown_application_privacy_values():
    db = _session()
    customer = _customer(db)
    db.add(
        PrivacyRequest(
            customer_id=customer.id,
            request_type="legacy_unknown",
            status="requested",
            result_url="",
            processed_at=None,
        )
    )

    with pytest.raises(HTTPException):
        db.commit()
    db.rollback()


def test_customer_export_renders_decimal_and_utc_without_precision_loss():
    rendered = privacy.render_customer_export(
        {
            "amount": Decimal("1234567890.01"),
            "generated_at": datetime(2026, 7, 31, 1, 2, 3, tzinfo=UTC),
            "nested": {"points": Decimal("0.10")},
        }
    )
    payload = json.loads(rendered)

    assert payload["amount"] == "1234567890.01"
    assert payload["nested"]["points"] == "0.10"
    assert payload["generated_at"] == "2026-07-31T01:02:03Z"
    assert "NaN" not in rendered


def test_customer_export_rejects_non_finite_values():
    with pytest.raises(ValueError):
        privacy.render_customer_export({"amount": Decimal("NaN")})


def test_mark_processed_requires_processing_transition():
    request = SimpleNamespace(status="requested", result_url="", processed_at=None)

    with pytest.raises(ValueError):
        privacy.mark_privacy_processed(request)

    request.status = "processing"
    privacy.mark_privacy_processed(request, " /api/privacy/export ")
    assert request.status == "processed"
    assert request.result_url == "/api/privacy/export"
    assert request.processed_at is not None


def test_anonymization_is_idempotent_for_deleted_customer():
    customer = SimpleNamespace(id=7, telegram_id="deleted:7:abcdef")

    result = privacy.anonymize_customer(SimpleNamespace(), customer)

    assert result["already_anonymized"] is True
    assert result["orders_anonymized"] == 0
    assert customer.telegram_id == "deleted:7:abcdef"


def test_privacy_writes_lock_customer_before_mutation():
    set_consent_source = inspect.getsource(privacy_api.set_consent)
    create_request_source = inspect.getsource(privacy_api.create_privacy_request)
    process_source = inspect.getsource(privacy_api.admin_process_privacy_request)

    assert set_consent_source.index("_lock_customer") < set_consent_source.index("ConsentRecord(")
    assert create_request_source.index("_lock_customer") < create_request_source.index("PrivacyRequest(")
    assert process_source.index("_lock_customer") < process_source.index("anonymize_customer")


def test_privacy_export_response_is_non_cacheable_attachment():
    source = inspect.getsource(privacy_api.export_my_data)

    assert "render_customer_export" in source
    assert '"Cache-Control": "no-store, max-age=0"' in source
    assert '"X-Content-Type-Options": "nosniff"' in source
    assert "attachment; filename=" in source


def test_migration_quarantines_legacy_rows_before_constraints():
    source = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0030_privacy_request_integrity.py"
    ).read_text(encoding="utf-8")

    first_repair = source.index("UPDATE privacy_requests")
    duplicate_repair = source.index("WITH ranked AS")
    first_constraint = source.index("op.create_check_constraint")

    assert first_repair < duplicate_repair < first_constraint
    assert "legacy_unknown" in source
    assert "uq_privacy_requests_open_customer_type" in source
    assert 'down_revision = "0029_catalog_moysklad_integrity"' in source
