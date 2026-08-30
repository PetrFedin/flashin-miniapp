from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.models import Customer
from backend.services import payment_settlement as settlement


class _CustomerQuery:
    def __init__(self, customer):
        self.customer = customer
        self.for_update = False

    def filter(self, *_args, **_kwargs):
        return self

    def with_for_update(self):
        self.for_update = True
        return self

    def first(self):
        return self.customer


class _CustomerDb:
    def __init__(self, customer):
        self.query_model = None
        self.query = _CustomerQuery(customer)

    def query(self, model):
        self.query_model = model
        return self.query


def _order(*, payment_status: str = "pending"):
    return SimpleNamespace(
        id=101,
        customer_id=7,
        status="created",
        payment_status=payment_status,
        items=[SimpleNamespace(variant_id=55, quantity=1)],
        loyalty_points_redeemed=0,
        total_amount=1000,
    )


def _disable_post_inventory_side_effects(monkeypatch):
    for name in (
        "queue_order_paid",
        "add_points",
        "mark_redemption_committed",
        "reward_referral_after_first_paid_order",
        "add_timeline_event",
        "ensure_fulfillment_task",
        "emit_event",
        "enqueue_webhook",
        "enqueue_event_for_destinations",
        "enqueue_moysklad_customer_order",
    ):
        monkeypatch.setattr(settlement, name, lambda *_args, **_kwargs: None)


def test_settlement_locks_customer_before_inventory(monkeypatch):
    events = []
    order = _order()

    def lock_customer(_db, customer_id):
        assert customer_id == order.customer_id
        events.append("customer")
        return SimpleNamespace(id=customer_id)

    def commit_inventory(_db, quantities, *, order_id, source):
        assert events == ["customer"]
        assert quantities == {55: 1}
        assert order_id == order.id
        assert source == "payment_settlement"
        events.append("inventory")

    monkeypatch.setattr(settlement, "_lock_settlement_customer", lock_customer)
    monkeypatch.setattr(settlement, "commit_reservations_to_sold", commit_inventory)
    _disable_post_inventory_side_effects(monkeypatch)

    assert settlement.settle_paid_order(object(), order) is True
    assert events == ["customer", "inventory"]
    assert order.status == "paid"
    assert order.payment_status == "paid"


def test_already_settled_order_does_not_acquire_customer_or_inventory_lock(monkeypatch):
    order = _order(payment_status="paid")

    monkeypatch.setattr(
        settlement,
        "_lock_settlement_customer",
        lambda *_args, **_kwargs: pytest.fail("settled order must not lock customer"),
    )
    monkeypatch.setattr(
        settlement,
        "commit_reservations_to_sold",
        lambda *_args, **_kwargs: pytest.fail("settled order must not lock inventory"),
    )

    assert settlement.settle_paid_order(object(), order) is False


def test_settlement_customer_lock_uses_for_update():
    customer = SimpleNamespace(id=7)
    db = _CustomerDb(customer)

    locked = settlement._lock_settlement_customer(db, 7)

    assert locked is customer
    assert db.query_model is Customer
    assert db.query.for_update is True


def test_missing_settlement_customer_fails_closed_before_inventory():
    db = _CustomerDb(None)

    with pytest.raises(HTTPException) as exc:
        settlement._lock_settlement_customer(db, 7)

    assert exc.value.status_code == 409
    assert exc.value.detail == "Settlement customer is missing"
    assert db.query.for_update is True
