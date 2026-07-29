from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import model_constraints  # noqa: F401
from backend.database import Base
from backend.models import Customer, Order, Payment
from backend.payment_attempt_models import PaymentCreationAttempt
from backend.services.payment_creation import (
    begin_payment_creation,
    finalize_payment_creation,
    load_claim_payment,
    mark_payment_creation_retry_required,
)
from backend.services.payments import _payment_idempotence_key


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _order(db, *, status="created", payment_status="pending", amount=1250.4):
    customer = Customer(telegram_id="payment-lease-user")
    db.add(customer)
    db.flush()
    order = Order(
        customer_id=customer.id,
        status=status,
        payment_status=payment_status,
        total_amount=amount,
        currency="RUB",
    )
    db.add(order)
    db.commit()
    return customer, order


def test_begin_allocates_durable_attempt_without_payment_row():
    db = _session()
    customer, order = _order(db)

    claim = begin_payment_creation(db, order.id, customer.id)
    db.commit()

    assert claim.attempt_id is not None
    assert claim.existing_payment_id is None
    attempt = db.query(PaymentCreationAttempt).one()
    assert attempt.id == claim.attempt_id
    assert attempt.status == "creating"
    assert attempt.lease_expires_at is not None
    assert db.query(Payment).count() == 0


def test_active_lease_rejects_parallel_creation_with_retry_after():
    db = _session()
    customer, order = _order(db)
    first = begin_payment_creation(db, order.id, customer.id)
    db.commit()

    with pytest.raises(HTTPException) as caught:
        begin_payment_creation(db, order.id, customer.id)

    assert caught.value.status_code == 409
    assert int(caught.value.headers["Retry-After"]) >= 1
    assert db.query(PaymentCreationAttempt).count() == 1
    assert db.query(PaymentCreationAttempt).one().id == first.attempt_id


def test_expired_lease_reuses_same_attempt_and_provider_key():
    db = _session()
    customer, order = _order(db)
    first = begin_payment_creation(db, order.id, customer.id)
    db.commit()

    attempt = db.query(PaymentCreationAttempt).one()
    attempt.lease_expires_at = datetime.utcnow() - timedelta(seconds=1)
    db.commit()

    second = begin_payment_creation(db, order.id, customer.id)
    db.commit()

    assert second.attempt_id == first.attempt_id
    assert _payment_idempotence_key(order.id, second.attempt_id) == _payment_idempotence_key(
        order.id,
        first.attempt_id,
    )
    assert db.query(PaymentCreationAttempt).count() == 1


def test_provider_failure_marks_retry_and_reuses_attempt():
    db = _session()
    customer, order = _order(db)
    first = begin_payment_creation(db, order.id, customer.id)
    db.commit()

    mark_payment_creation_retry_required(db, first.attempt_id, "network_error")
    db.commit()
    failed = db.query(PaymentCreationAttempt).one()
    assert failed.status == "retry_required"
    assert failed.lease_expires_at is None
    assert failed.last_error == "network_error"

    second = begin_payment_creation(db, order.id, customer.id)
    db.commit()
    assert second.attempt_id == first.attempt_id
    assert db.query(PaymentCreationAttempt).one().status == "creating"


def test_finalize_creates_payment_and_completes_attempt():
    db = _session()
    customer, order = _order(db)
    claim = begin_payment_creation(db, order.id, customer.id)
    db.commit()

    finalized_order, payment = finalize_payment_creation(
        db,
        claim.attempt_id,
        "provider-payment-1",
        "pending",
        "https://example.test/pay",
    )
    db.commit()

    attempt = db.query(PaymentCreationAttempt).one()
    assert attempt.status == "completed"
    assert attempt.provider_payment_id == "provider-payment-1"
    assert attempt.lease_expires_at is None
    assert payment.order_id == order.id
    assert finalized_order.status == "payment_created"
    assert finalized_order.payment_status == "payment_created"

    repeated_order, repeated_payment = finalize_payment_creation(
        db,
        claim.attempt_id,
        "provider-payment-1",
        "pending",
        "https://example.test/pay",
    )
    assert repeated_order.id == finalized_order.id
    assert repeated_payment.id == payment.id
    assert db.query(Payment).count() == 1


def test_existing_reusable_payment_short_circuits_new_provider_call():
    db = _session()
    customer, order = _order(db, status="payment_created", payment_status="payment_created")
    payment = Payment(
        order_id=order.id,
        provider="yookassa",
        provider_payment_id="provider-existing",
        status="pending",
        amount=order.total_amount,
        confirmation_url="https://example.test/existing",
    )
    db.add(payment)
    db.commit()

    claim = begin_payment_creation(db, order.id, customer.id)
    assert claim.is_existing is True
    loaded_order, loaded_payment = load_claim_payment(db, claim)
    assert loaded_order.id == order.id
    assert loaded_payment.id == payment.id
    assert db.query(PaymentCreationAttempt).count() == 0


def test_webhook_race_reuses_provider_payment_instead_of_duplicate():
    db = _session()
    customer, order = _order(db)
    claim = begin_payment_creation(db, order.id, customer.id)
    db.commit()

    webhook_payment = Payment(
        order_id=order.id,
        provider="yookassa",
        provider_payment_id="provider-race",
        status="succeeded",
        amount=order.total_amount,
    )
    db.add(webhook_payment)
    order.status = "paid"
    order.payment_status = "paid"
    db.commit()

    finalized_order, finalized_payment = finalize_payment_creation(
        db,
        claim.attempt_id,
        "provider-race",
        "succeeded",
        "",
    )
    db.commit()

    assert finalized_payment.id == webhook_payment.id
    assert finalized_order.status == "paid"
    assert db.query(Payment).count() == 1
    assert db.query(PaymentCreationAttempt).one().status == "completed"


def test_cancel_during_provider_call_routes_non_canceled_payment_to_review():
    db = _session()
    customer, order = _order(db)
    claim = begin_payment_creation(db, order.id, customer.id)
    db.commit()

    order = db.query(Order).filter(Order.id == order.id).one()
    order.status = "cancelled"
    order.payment_status = "cancelled"
    db.commit()

    finalized_order, payment = finalize_payment_creation(
        db,
        claim.attempt_id,
        "provider-after-cancel",
        "pending",
        "https://example.test/pay",
    )
    db.commit()

    assert payment.provider_payment_id == "provider-after-cancel"
    assert finalized_order.status == "payment_review_required"
    assert finalized_order.payment_status == "payment_review_required"


def test_canceled_provider_result_preserves_canceled_order():
    db = _session()
    customer, order = _order(db)
    claim = begin_payment_creation(db, order.id, customer.id)
    db.commit()

    order = db.query(Order).filter(Order.id == order.id).one()
    order.status = "cancelled"
    order.payment_status = "cancelled"
    db.commit()

    finalized_order, payment = finalize_payment_creation(
        db,
        claim.attempt_id,
        "provider-canceled",
        "canceled",
        "",
    )
    db.commit()

    assert payment.status == "canceled"
    assert finalized_order.status == "cancelled"
    assert finalized_order.payment_status == "cancelled"
