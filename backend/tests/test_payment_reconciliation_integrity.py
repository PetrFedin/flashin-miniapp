from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import model_constraints  # noqa: F401
from backend.database import Base
from backend.models import Customer, Order, Payment, PaymentReconciliation
from backend.services.payment_reconciliation import (
    create_reconciliation_row,
    parse_provider_payment_contract,
    resolve_reconciliation,
)


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _payment(db, *, status="pending", amount=100.0, currency="RUB"):
    customer = Customer(telegram_id="reconciliation-user")
    db.add(customer)
    db.flush()
    order = Order(
        customer_id=customer.id,
        status="payment_created",
        payment_status="payment_created",
        total_amount=amount,
        currency=currency,
    )
    db.add(order)
    db.flush()
    payment = Payment(
        order_id=order.id,
        provider="yookassa",
        provider_payment_id="pay-reconciliation",
        status=status,
        amount=amount,
    )
    db.add(payment)
    db.commit()
    return payment


def test_provider_contract_is_strict_and_normalized():
    status, amount, currency = parse_provider_payment_contract(
        {
            "id": "pay-reconciliation",
            "status": " SUCCEEDED ",
            "amount": {"value": "100.00", "currency": "rub"},
        },
        "pay-reconciliation",
    )
    assert status == "succeeded"
    assert amount == Decimal("100.00")
    assert currency == "RUB"


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {"id": "other", "status": "pending", "amount": {"value": "100", "currency": "RUB"}},
        {"id": "pay-reconciliation", "status": "unknown", "amount": {"value": "100", "currency": "RUB"}},
        {"id": "pay-reconciliation", "status": "pending", "amount": {"value": "nan", "currency": "RUB"}},
        {"id": "pay-reconciliation", "status": "pending", "amount": {"value": "100", "currency": ""}},
    ],
)
def test_provider_contract_rejects_invalid_payload(payload):
    with pytest.raises(HTTPException):
        parse_provider_payment_contract(payload, "pay-reconciliation")


def test_identical_reconciliation_is_idempotent():
    db = _session()
    payment = _payment(db)

    first = create_reconciliation_row(
        db,
        payment.id,
        payment.provider_payment_id,
        "pending",
        Decimal("100.00"),
        "RUB",
    )
    db.commit()
    first_id = first.id

    second = create_reconciliation_row(
        db,
        payment.id,
        payment.provider_payment_id,
        "pending",
        Decimal("100.00"),
        "RUB",
    )
    db.commit()

    assert second.id == first_id
    assert second.status == "matched"
    assert db.query(PaymentReconciliation).count() == 1


def test_changed_reconciliation_creates_a_new_audit_snapshot():
    db = _session()
    payment = _payment(db)

    first = create_reconciliation_row(
        db,
        payment.id,
        payment.provider_payment_id,
        "pending",
        Decimal("100.00"),
        "RUB",
    )
    db.commit()

    second = create_reconciliation_row(
        db,
        payment.id,
        payment.provider_payment_id,
        "succeeded",
        Decimal("100.00"),
        "RUB",
    )
    db.commit()

    assert second.id != first.id
    assert second.status == "mismatch"
    assert "status" in second.message
    assert db.query(PaymentReconciliation).count() == 2


def test_amount_and_currency_mismatches_are_exact():
    db = _session()
    payment = _payment(db, amount=10.01)

    row = create_reconciliation_row(
        db,
        payment.id,
        payment.provider_payment_id,
        "pending",
        Decimal("10.02"),
        "USD",
    )
    db.commit()

    assert row.status == "mismatch"
    assert "amount 10.01 != 10.02" in row.message
    assert "currency 'RUB' != 'USD'" in row.message


def test_reconciliation_detects_payment_changed_during_provider_request():
    db = _session()
    payment = _payment(db)

    payment.provider_payment_id = "pay-replaced"
    db.commit()

    with pytest.raises(HTTPException) as caught:
        create_reconciliation_row(
            db,
            payment.id,
            "pay-reconciliation",
            "pending",
            Decimal("100.00"),
            "RUB",
        )
    assert caught.value.status_code == 409


def test_resolution_is_idempotent_but_cannot_be_rewritten():
    row = PaymentReconciliation(status="mismatch", message="original")

    assert resolve_reconciliation(row, "checked manually") is True
    assert row.status == "resolved"
    assert row.resolved_at is not None
    assert resolve_reconciliation(row, "checked manually") is False

    with pytest.raises(HTTPException) as caught:
        resolve_reconciliation(row, "different conclusion")
    assert caught.value.status_code == 409


def test_resolution_message_is_bounded():
    row = PaymentReconciliation(status="mismatch")
    with pytest.raises(HTTPException) as caught:
        resolve_reconciliation(row, "x" * 2001)
    assert caught.value.status_code == 400
