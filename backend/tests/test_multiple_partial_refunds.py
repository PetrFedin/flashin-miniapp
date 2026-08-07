from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend import model_constraints  # noqa: F401
from backend.database import Base
from backend.models import Customer, Order, ReturnRequest
from backend.services import refund_state
from backend.services.payments import _refund_idempotence_key


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _order(db, total=100.0):
    customer = Customer(telegram_id="10001")
    db.add(customer)
    db.flush()
    order = Order(
        customer_id=customer.id,
        status="partially_refunded",
        payment_status="partially_refunded",
        total_amount=total,
        currency="RUB",
    )
    db.add(order)
    db.flush()
    return order


def test_model_allows_multiple_return_requests_per_order():
    constraint_names = {
        constraint.name
        for constraint in ReturnRequest.__table__.constraints
        if constraint.name
    }

    assert "uq_return_requests_order_id" not in constraint_names
    assert "uq_return_requests_provider_refund_id" in {
        index.name for index in ReturnRequest.__table__.indexes
    }


def test_remaining_refundable_amount_uses_completed_partial_refunds():
    db = _session()
    order = _order(db)
    db.add(
        ReturnRequest(
            order_id=order.id,
            customer_id=order.customer_id,
            reason="first partial return",
            status="approved_partial",
            refund_amount=30.0,
            provider_refund_id="refund-1",
        )
    )
    db.commit()

    assert refund_state.remaining_refundable_amount(db, order) == refund_state.refund_money(
        70,
        "expected",
    )


def test_second_refund_completes_order_cumulatively(monkeypatch):
    db = _session()
    order = _order(db)
    db.add(
        ReturnRequest(
            order_id=order.id,
            customer_id=order.customer_id,
            reason="first partial return",
            status="approved_partial",
            refund_amount=30.0,
            provider_refund_id="refund-1",
        )
    )
    second = ReturnRequest(
        order_id=order.id,
        customer_id=order.customer_id,
        reason="remaining return",
        status="processing",
        refund_amount=70.0,
        provider_refund_id="refund-2",
    )
    db.add(second)
    db.flush()
    monkeypatch.setattr(
        refund_state,
        "apply_full_refund_loyalty",
        lambda *args, **kwargs: {"loyalty_reversed": True},
    )
    monkeypatch.setattr(refund_state, "_order_item_quantities", lambda _order: {77: 2})
    restored = []

    def fake_restore_sold_variants(_db, quantities, *, order_id, source):
        restored.append((dict(quantities), order_id, source))
        return True

    monkeypatch.setattr(refund_state, "restore_sold_variants", fake_restore_sold_variants)

    result = refund_state.apply_provider_refund_status(db, second, order, "succeeded")

    assert second.status == "approved"
    assert order.status == "refunded"
    assert order.payment_status == "refunded"
    assert result["cumulative_refund_amount"] == 100.0
    assert result["remaining_refundable_amount"] == 0.0
    assert result["inventory_restored"] is True
    assert restored == [({77: 2}, order.id, "full_refund")]


def test_equal_refund_amounts_have_distinct_provider_idempotency_keys():
    first = _refund_idempotence_key("payment-1", 10, 101, 25.0, "RUB")
    second = _refund_idempotence_key("payment-1", 10, 102, 25.0, "RUB")

    assert first != second
