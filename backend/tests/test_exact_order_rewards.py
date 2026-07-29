from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import model_constraints  # noqa: F401
from backend.database import Base
from backend.models import CrmProfile, Customer, LoyaltyTransaction, Order, Payment
from backend.services import loyalty


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _paid_order(db, total=100.01):
    customer = Customer(telegram_id=f"reward-{db.query(Customer).count()}")
    db.add(customer)
    db.flush()
    order = Order(
        customer_id=customer.id,
        status="payment_created",
        payment_status="payment_created",
        total_amount=total,
        currency="RUB",
    )
    db.add(order)
    db.flush()
    payment = Payment(
        order_id=order.id,
        provider="yookassa",
        provider_payment_id=f"pay-reward-{order.id}",
        status="succeeded",
        amount=total,
    )
    db.add(payment)
    db.commit()
    return customer, order


def test_reward_formula_preserves_four_decimal_point_scale():
    assert loyalty.calculate_order_reward_points(100.01, 0.01) == Decimal("1.0001")
    assert loyalty.calculate_order_reward_points(0.05, 0.01) == Decimal("0.0005")


def test_paid_order_reward_ignores_pre_rounded_caller_value(monkeypatch):
    db = _session()
    customer, order = _paid_order(db, total=100.01)
    monkeypatch.setattr(
        loyalty,
        "get_settings",
        lambda: SimpleNamespace(loyalty_points_per_ruble=0.01),
    )

    loyalty.add_points(db, customer.id, 1.00, "order_paid", order.id)
    db.commit()

    profile = db.query(CrmProfile).filter(CrmProfile.customer_id == customer.id).one()
    transaction = db.query(LoyaltyTransaction).one()
    assert profile.loyalty_points == pytest.approx(1.0001)
    assert transaction.points_delta == pytest.approx(1.0001)


def test_paid_order_reward_requires_matching_customer(monkeypatch):
    db = _session()
    customer, order = _paid_order(db)
    other = Customer(telegram_id="reward-other")
    db.add(other)
    db.commit()
    monkeypatch.setattr(
        loyalty,
        "get_settings",
        lambda: SimpleNamespace(loyalty_points_per_ruble=0.01),
    )

    with pytest.raises(HTTPException) as caught:
        loyalty.add_points(db, other.id, 1, "order_paid", order.id)
    assert caught.value.status_code == 409
    assert db.query(LoyaltyTransaction).count() == 0
    assert db.query(CrmProfile).filter(CrmProfile.customer_id == customer.id).count() == 0


def test_paid_order_reward_requires_successful_payment(monkeypatch):
    db = _session()
    customer, order = _paid_order(db)
    payment = db.query(Payment).filter(Payment.order_id == order.id).one()
    payment.status = "pending"
    db.commit()
    monkeypatch.setattr(
        loyalty,
        "get_settings",
        lambda: SimpleNamespace(loyalty_points_per_ruble=0.01),
    )

    with pytest.raises(HTTPException) as caught:
        loyalty.add_points(db, customer.id, 1, "order_paid", order.id)
    assert caught.value.status_code == 409
    assert db.query(LoyaltyTransaction).count() == 0


@pytest.mark.parametrize("rate", [-0.01, 1000.0001, float("nan"), float("inf")])
def test_invalid_reward_rate_fails_closed(rate):
    with pytest.raises(HTTPException) as caught:
        loyalty.calculate_order_reward_points(100, rate)
    assert caught.value.status_code in {400, 500}


def test_zero_total_creates_no_reward_transaction(monkeypatch):
    db = _session()
    customer, order = _paid_order(db, total=0)
    payment = db.query(Payment).filter(Payment.order_id == order.id).one()
    payment.amount = 0.01
    db.commit()
    monkeypatch.setattr(
        loyalty,
        "get_settings",
        lambda: SimpleNamespace(loyalty_points_per_ruble=0.01),
    )

    loyalty.add_points(db, customer.id, 999, "order_paid", order.id)
    db.commit()
    assert db.query(LoyaltyTransaction).count() == 0
