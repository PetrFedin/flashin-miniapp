import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import model_constraints  # noqa: F401
from backend.api.order_cancellation import _cancel_before_payment
from backend.database import Base
from backend.models import Customer, Order
from backend.payment_attempt_models import PaymentCreationAttempt


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_direct_cancellation_is_blocked_after_payment_attempt_is_claimed():
    db = _session()
    customer = Customer(telegram_id="cancel-guard-user")
    db.add(customer)
    db.flush()
    order = Order(
        customer_id=customer.id,
        status="created",
        payment_status="pending",
        total_amount=100.0,
        currency="RUB",
    )
    db.add(order)
    db.flush()
    db.add(
        PaymentCreationAttempt(
            order_id=order.id,
            provider="yookassa",
            attempt_number=1,
            status="creating",
        )
    )
    db.commit()

    with pytest.raises(HTTPException) as caught:
        _cancel_before_payment(db, order)

    assert caught.value.status_code == 409
    assert "reconcile payment" in str(caught.value.detail)
    assert order.status == "created"
    assert order.payment_status == "pending"
