from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend import model_constraints  # noqa: F401
from backend.api import payments as payments_api
from backend.database import Base
from backend.models import Customer, Order
from backend.payment_attempt_models import PaymentCreationAttempt
from backend.payment_attempt_statuses import (
    ABANDONED_PAYMENT_ATTEMPT_STATUS,
    COMPLETED_PAYMENT_ATTEMPT_STATUS,
    CREATING_PAYMENT_ATTEMPT_STATUS,
    RETRY_REQUIRED_PAYMENT_ATTEMPT_STATUS,
    REVIEW_REQUIRED_PAYMENT_ATTEMPT_STATUS,
)
from backend.schemas import PaymentCreate
from backend.services.payment_creation import (
    begin_payment_creation,
    finalize_payment_creation,
    mark_payment_creation_review_required,
)


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _order(db, *, suffix: str = "1"):
    customer = Customer(telegram_id=f"payment-attempt-integrity-{suffix}")
    db.add(customer)
    db.flush()
    order = Order(
        customer_id=customer.id,
        status="created",
        payment_status="pending",
        delivery_status="not_started",
        total_amount=1250.4,
        delivery_price=0,
        discount_amount=0,
        loyalty_points_redeemed=0,
        loyalty_discount_amount=0,
        currency="RUB",
        delivery_type="pickup",
        address="",
        comment="",
        tracking_number="",
    )
    db.add(order)
    db.commit()
    return customer, order


def _attempt_values(**overrides):
    values = {
        "order_id": 1,
        "provider": "yookassa",
        "attempt_number": 1,
        "status": CREATING_PAYMENT_ATTEMPT_STATUS,
        "provider_payment_id": "",
        "lease_expires_at": datetime.utcnow() + timedelta(minutes=1),
        "last_error": "",
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    "overrides",
    [
        {"attempt_number": 0},
        {"provider": " YooKassa "},
        {"status": "unknown"},
        {"status": CREATING_PAYMENT_ATTEMPT_STATUS, "lease_expires_at": None},
        {
            "status": RETRY_REQUIRED_PAYMENT_ATTEMPT_STATUS,
            "lease_expires_at": None,
            "last_error": "",
        },
        {
            "status": REVIEW_REQUIRED_PAYMENT_ATTEMPT_STATUS,
            "lease_expires_at": None,
            "last_error": "",
        },
        {
            "status": COMPLETED_PAYMENT_ATTEMPT_STATUS,
            "lease_expires_at": None,
            "provider_payment_id": "",
        },
        {
            "status": ABANDONED_PAYMENT_ATTEMPT_STATUS,
            "lease_expires_at": None,
            "last_error": "",
        },
    ],
)
def test_database_rejects_impossible_payment_attempt_states(overrides):
    db = _session()
    with pytest.raises(IntegrityError):
        db.execute(PaymentCreationAttempt.__table__.insert().values(**_attempt_values(**overrides)))
        db.commit()
    db.rollback()


def test_database_allows_one_open_attempt_and_rejects_a_duplicate():
    db = _session()
    db.execute(PaymentCreationAttempt.__table__.insert().values(**_attempt_values()))
    db.commit()

    duplicate = _attempt_values(
        attempt_number=2,
        status=RETRY_REQUIRED_PAYMENT_ATTEMPT_STATUS,
        lease_expires_at=None,
        last_error="network_error",
    )
    with pytest.raises(IntegrityError):
        db.execute(PaymentCreationAttempt.__table__.insert().values(**duplicate))
        db.commit()
    db.rollback()

    assert db.query(PaymentCreationAttempt).count() == 1


def test_review_required_attempt_blocks_automatic_retry_and_finalization():
    db = _session()
    customer, order = _order(db)
    claim = begin_payment_creation(db, order.id, customer.id)
    db.commit()

    mark_payment_creation_review_required(db, claim.attempt_id, "provider_contract_mismatch")
    db.commit()

    with pytest.raises(HTTPException) as retry_error:
        begin_payment_creation(db, order.id, customer.id)
    assert retry_error.value.status_code == 409
    assert "manual review" in str(retry_error.value.detail).lower()

    with pytest.raises(HTTPException) as finalize_error:
        finalize_payment_creation(
            db,
            claim.attempt_id,
            "provider-payment-review",
            "pending",
            "https://example.test/pay",
        )
    assert finalize_error.value.status_code == 409
    assert db.query(PaymentCreationAttempt).one().status == REVIEW_REQUIRED_PAYMENT_ATTEMPT_STATUS


def test_transient_provider_failure_persists_retry_required(monkeypatch):
    db = _session()
    customer, order = _order(db, suffix="transient")

    async def fail(*_args, **_kwargs):
        raise HTTPException(
            status_code=502,
            detail={"provider": "yookassa", "error": "network_error"},
        )

    monkeypatch.setattr(payments_api, "create_yookassa_payment", fail)

    with pytest.raises(HTTPException):
        asyncio.run(
            payments_api.create_payment(
                PaymentCreate(order_id=order.id),
                customer=customer,
                db=db,
            )
        )

    db.expire_all()
    attempt = db.query(PaymentCreationAttempt).one()
    assert attempt.status == RETRY_REQUIRED_PAYMENT_ATTEMPT_STATUS
    assert attempt.lease_expires_at is None
    assert '"error":"network_error"' in attempt.last_error


def test_permanent_provider_failure_persists_review_required(monkeypatch):
    db = _session()
    customer, order = _order(db, suffix="permanent")

    async def fail(*_args, **_kwargs):
        raise HTTPException(
            status_code=502,
            detail={
                "provider": "yookassa",
                "error": "provider_rejected_request",
                "status_code": 400,
            },
        )

    monkeypatch.setattr(payments_api, "create_yookassa_payment", fail)

    with pytest.raises(HTTPException):
        asyncio.run(
            payments_api.create_payment(
                PaymentCreate(order_id=order.id),
                customer=customer,
                db=db,
            )
        )

    db.expire_all()
    attempt = db.query(PaymentCreationAttempt).one()
    assert attempt.status == REVIEW_REQUIRED_PAYMENT_ATTEMPT_STATUS
    assert attempt.lease_expires_at is None
    assert '"status_code":400' in attempt.last_error


def test_unexpected_provider_failure_is_not_retried_automatically(monkeypatch):
    db = _session()
    customer, order = _order(db, suffix="unexpected")

    async def fail(*_args, **_kwargs):
        raise RuntimeError("unexpected provider parser failure")

    monkeypatch.setattr(payments_api, "create_yookassa_payment", fail)

    with pytest.raises(RuntimeError):
        asyncio.run(
            payments_api.create_payment(
                PaymentCreate(order_id=order.id),
                customer=customer,
                db=db,
            )
        )

    db.expire_all()
    attempt = db.query(PaymentCreationAttempt).one()
    assert attempt.status == REVIEW_REQUIRED_PAYMENT_ATTEMPT_STATUS
    assert attempt.last_error == "unexpected_provider_failure:RuntimeError"


def test_metadata_contains_payment_attempt_constraints_and_open_index():
    constraints = {constraint.name for constraint in PaymentCreationAttempt.__table__.constraints}
    indexes = {index.name for index in PaymentCreationAttempt.__table__.indexes}

    assert {
        "ck_payment_creation_attempts_number_positive",
        "ck_payment_creation_attempts_provider_normalized",
        "ck_payment_creation_attempts_status_valid",
        "ck_payment_creation_attempts_creating_lease_required",
        "ck_payment_creation_attempts_noncreating_lease_empty",
        "ck_payment_creation_attempts_completed_provider_id_required",
        "ck_payment_creation_attempts_failure_error_required",
    }.issubset(constraints)
    assert "uq_payment_creation_attempts_one_open" in indexes


def test_payment_attempt_migration_uses_collision_safe_legacy_repair():
    source = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0022_payment_attempt_integrity.py"
    ).read_text(encoding="utf-8")

    temporary_number_position = source.index("SET attempt_number = -id")
    provider_normalization_position = source.index("SET provider = CASE")
    final_number_position = source.index("SET attempt_number = renumbered.normalized_number")
    constraint_position = source.index("op.create_check_constraint")

    assert temporary_number_position < provider_normalization_position < final_number_position
    assert final_number_position < constraint_position
    assert "superseded duplicate open attempt" in source
    assert "uq_payment_creation_attempts_one_open" in source
