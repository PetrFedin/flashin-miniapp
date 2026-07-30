from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from backend import model_constraints  # noqa: F401
from backend.database import Base
from backend.models import Customer, Order
from backend.order_statuses import DELIVERY_STATUSES, ORDER_STATUSES, PAYMENT_STATUSES


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _persist_order(db, *, status: str, payment_status: str, delivery_status: str, suffix: str):
    customer = Customer(telegram_id=f"order-state-{suffix}")
    db.add(customer)
    db.flush()
    order = Order(
        customer_id=customer.id,
        status=status,
        payment_status=payment_status,
        delivery_status=delivery_status,
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
    db.commit()
    return order


@pytest.mark.parametrize(
    "status,payment_status,delivery_status",
    [
        ("created", "pending", "not_started"),
        ("payment_created", "payment_created", "not_started"),
        ("payment_review_required", "payment_review_required", "not_started"),
        ("payment_review_required", "paid_review_required", "not_started"),
        ("paid", "paid", "not_started"),
        ("assembling", "paid", "assembling"),
        ("ready", "paid", "ready"),
        ("ready", "paid", "cancelled"),
        ("shipped", "paid", "shipped"),
        ("shipped", "paid", "delivery_failed"),
        ("shipped", "paid", "returned"),
        ("completed", "paid", "delivered"),
        ("refund_requested", "paid", "delivered"),
        ("refund_requested", "refund_processing", "delivered"),
        ("refund_requested", "refund_retry_required", "delivered"),
        ("refund_requested", "refund_pending", "delivered"),
        ("refund_requested", "refund_review_required", "delivered"),
        ("partially_refunded", "partially_refunded", "delivered"),
        ("refunded", "refunded", "delivered"),
        ("cancelled", "cancelled", "not_started"),
        ("cancelled", "cancelled", "cancelled"),
    ],
)
def test_valid_order_state_matrix_is_persistable(status, payment_status, delivery_status):
    db = _session()
    order = _persist_order(
        db,
        status=status,
        payment_status=payment_status,
        delivery_status=delivery_status,
        suffix=f"{status}-{payment_status}-{delivery_status}",
    )

    assert order.status == status
    assert order.payment_status == payment_status
    assert order.delivery_status == delivery_status


@pytest.mark.parametrize(
    "status,payment_status,delivery_status",
    [
        ("unknown", "pending", "not_started"),
        ("created", "unknown", "not_started"),
        ("created", "pending", "unknown"),
        ("created", "paid", "not_started"),
        ("payment_created", "pending", "not_started"),
        ("payment_review_required", "paid", "not_started"),
        ("paid", "pending", "not_started"),
        ("assembling", "paid", "not_started"),
        ("ready", "paid", "shipped"),
        ("shipped", "paid", "delivered"),
        ("completed", "paid", "shipped"),
        ("refund_requested", "pending", "delivered"),
        ("partially_refunded", "paid", "delivered"),
        ("refunded", "partially_refunded", "delivered"),
        ("cancelled", "cancelled", "delivered"),
    ],
)
def test_invalid_or_incoherent_order_states_are_rejected(status, payment_status, delivery_status):
    db = _session()
    with pytest.raises(IntegrityError):
        _persist_order(
            db,
            status=status,
            payment_status=payment_status,
            delivery_status=delivery_status,
            suffix=f"invalid-{status}-{payment_status}-{delivery_status}",
        )
    db.rollback()
    assert db.query(Order).count() == 0


def test_status_catalogs_are_normalized_and_non_overlapping_by_domain():
    for values in (ORDER_STATUSES, PAYMENT_STATUSES, DELIVERY_STATUSES):
        assert values
        assert all(value == value.strip().lower() for value in values)
        assert all(value for value in values)


def test_order_metadata_contains_state_constraints():
    names = {constraint.name for constraint in Order.__table__.constraints}
    assert {
        "ck_orders_status_valid",
        "ck_orders_payment_status_valid",
        "ck_orders_delivery_status_valid",
        "ck_orders_payment_state_coherent",
        "ck_orders_delivery_state_coherent",
    }.issubset(names)


def test_order_state_migration_repairs_before_enforcing_constraints():
    source = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0023_order_state_integrity.py"
    ).read_text(encoding="utf-8")

    normalization_position = source.index("UPDATE orders")
    authoritative_payment_position = source.index("Provider/payment state is more authoritative")
    exact_pair_position = source.index("Make the payment label exact")
    delivery_repair_position = source.index("Operational order stages own the delivery stage")
    constraint_position = source.index("op.create_check_constraint")

    assert normalization_position < authoritative_payment_position
    assert authoritative_payment_position < exact_pair_position < delivery_repair_position
    assert delivery_repair_position < constraint_position
    assert "refund_retry_required" in source
    assert "ck_orders_payment_state_coherent" in source
