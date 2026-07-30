from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import AdminUser, Customer, Order, Payment, ReturnRequest
from backend.payment_statuses import PROVIDER_PAYMENT_STATUSES
from backend.schemas import RefundApproveIn
from backend.api import returns as returns_api


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _paid_order(db, *, suffix: str = "1"):
    customer = Customer(telegram_id=f"payment-record-{suffix}")
    admin = AdminUser(
        email=f"admin-{suffix}@example.test",
        password_hash="not-used",
        role="admin",
        active=True,
    )
    db.add_all([customer, admin])
    db.flush()
    order = Order(
        customer_id=customer.id,
        status="paid",
        payment_status="paid",
        delivery_status="not_started",
        total_amount=100,
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
    db.flush()
    request = ReturnRequest(
        order_id=order.id,
        customer_id=customer.id,
        reason="Item does not fit",
        status="requested",
        provider_refund_id="",
        refund_amount=0,
    )
    db.add(request)
    db.commit()
    return customer, admin, order, request


def test_orm_normalizes_provider_payment_record_before_insert():
    db = _session()
    _customer, _admin, order, _request = _paid_order(db)
    payment = Payment(
        order_id=order.id,
        provider=" YooKassa ",
        provider_payment_id=" payment-1 ",
        status=" PAID ",
        amount=100,
        confirmation_url=" https://example.test/pay ",
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)

    assert payment.provider == "yookassa"
    assert payment.provider_payment_id == "payment-1"
    assert payment.status == "succeeded"
    assert payment.confirmation_url == "https://example.test/pay"


@pytest.mark.parametrize(
    "values",
    [
        {"provider": "", "provider_payment_id": "payment-1", "status": "pending"},
        {"provider": " YooKassa ", "provider_payment_id": "payment-1", "status": "pending"},
        {"provider": "yookassa", "provider_payment_id": "", "status": "pending"},
        {"provider": "yookassa", "provider_payment_id": " payment-1 ", "status": "pending"},
        {"provider": "yookassa", "provider_payment_id": "payment-1", "status": "paid"},
        {"provider": "yookassa", "provider_payment_id": "payment-1", "status": "unknown"},
        {
            "provider": "yookassa",
            "provider_payment_id": "payment-1",
            "status": "pending",
            "confirmation_url": " https://example.test/pay ",
        },
    ],
)
def test_direct_sql_cannot_bypass_payment_record_constraints(values):
    db = _session()
    _customer, _admin, order, _request = _paid_order(db, suffix=str(abs(hash(str(values)))))
    payload = {
        "order_id": order.id,
        "provider": "yookassa",
        "provider_payment_id": "payment-default",
        "status": "pending",
        "amount": 100,
        "confirmation_url": "",
    }
    payload.update(values)

    with pytest.raises(IntegrityError):
        db.execute(Payment.__table__.insert().values(**payload))
        db.commit()
    db.rollback()


def test_normalized_duplicate_provider_payment_id_is_rejected():
    db = _session()
    _customer, _admin, order, _request = _paid_order(db)
    db.add(
        Payment(
            order_id=order.id,
            provider="yookassa",
            provider_payment_id="payment-duplicate",
            status="pending",
            amount=100,
        )
    )
    db.commit()

    db.add(
        Payment(
            order_id=order.id,
            provider=" YooKassa ",
            provider_payment_id=" payment-duplicate ",
            status="pending",
            amount=100,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    assert db.query(Payment).count() == 1


@pytest.mark.parametrize("payment_status", ["pending", "waiting_for_capture", "canceled"])
def test_refund_approval_rejects_non_succeeded_provider_payments(monkeypatch, payment_status):
    db = _session()
    _customer, admin, order, request = _paid_order(db, suffix=payment_status)
    db.add(
        Payment(
            order_id=order.id,
            provider="yookassa",
            provider_payment_id=f"payment-{payment_status}",
            status=payment_status,
            amount=100,
        )
    )
    db.commit()

    monkeypatch.setattr(returns_api, "require_permission", lambda *_args, **_kwargs: None)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            returns_api.approve_return(
                RefundApproveIn(return_id=request.id, amount=100),
                admin=admin,
                db=db,
            )
        )

    assert caught.value.status_code == 409
    assert "succeeded yookassa payment" in str(caught.value.detail).lower()
    db.expire_all()
    saved_request = db.query(ReturnRequest).filter(ReturnRequest.id == request.id).one()
    saved_order = db.query(Order).filter(Order.id == order.id).one()
    assert saved_request.status == "requested"
    assert saved_request.refund_amount == 0
    assert saved_order.status == "paid"
    assert saved_order.payment_status == "paid"


def test_payment_status_catalog_is_normalized_and_exact():
    assert PROVIDER_PAYMENT_STATUSES == {
        "pending",
        "waiting_for_capture",
        "succeeded",
        "canceled",
    }


def test_payment_metadata_contains_record_constraints():
    names = {constraint.name for constraint in Payment.__table__.constraints}
    assert {
        "ck_payments_amount_positive",
        "ck_payments_provider_normalized",
        "ck_payments_provider_payment_id_normalized",
        "ck_payments_status_valid",
        "ck_payments_confirmation_url_normalized",
    }.issubset(names)


def test_payment_migration_quarantines_empty_and_duplicate_legacy_rows_safely():
    source = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0024_payment_record_integrity.py"
    ).read_text(encoding="utf-8")

    mapping_position = source.index("CREATE TEMP TABLE payment_record_normalization_map")
    temporary_position = source.index("provider = 'migration_tmp'")
    quarantine_position = source.index("candidate_provider := 'legacy_unresolved'")
    restore_position = source.index("SET provider = mapping.final_provider")
    constraint_position = source.index("op.create_check_constraint")

    assert mapping_position < temporary_position < quarantine_position < restore_position
    assert restore_position < constraint_position
    assert "duplicate_rank = 1" in source
    assert "final_provider = candidate_provider" in source
