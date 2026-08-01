from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.models import LoyaltyRedemptionHold, Payment, PromoCode
from backend.services import order_cancellation


class FakeQuery:
    def __init__(self, value):
        self.value = value

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self):
        return self

    def first(self):
        if isinstance(self.value, list):
            return self.value[0] if self.value else None
        return self.value

    def all(self):
        if isinstance(self.value, list):
            return self.value
        return [] if self.value is None else [self.value]


class FakeSession:
    def __init__(self, *, payment=None, promo=None, holds=None):
        self.payment = payment
        self.promo = promo
        self.holds = list(holds or [])
        self.queries = []

    def query(self, entity):
        self.queries.append(entity)
        if entity is Payment.id:
            return FakeQuery(self.payment)
        if entity is PromoCode:
            return FakeQuery(self.promo)
        if entity is LoyaltyRedemptionHold:
            return FakeQuery(self.holds)
        raise AssertionError(f"Unexpected query: {entity}")


def make_order(**overrides):
    values = {
        "id": 41,
        "customer_id": 7,
        "promo_code_id": 3,
        "status": "payment_created",
        "payment_status": "payment_created",
        "delivery_status": "not_started",
        "items": [
            SimpleNamespace(id=1, variant_id=11, quantity=2),
            SimpleNamespace(id=2, variant_id=12, quantity=1),
        ],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_provider_cancellation_releases_every_linked_resource(monkeypatch):
    promo = SimpleNamespace(id=3, used_count=4)
    holds = [
        SimpleNamespace(status="reserved", released_at=None),
        SimpleNamespace(status="reserved", released_at=None),
    ]
    db = FakeSession(promo=promo, holds=holds)
    order = make_order()
    released_at = datetime(2026, 8, 1, 20, 0, 0)
    released = []
    notifications = []
    monkeypatch.setattr(
        order_cancellation,
        "release_variants",
        lambda _db, quantities: released.append(quantities),
    )
    monkeypatch.setattr(order_cancellation, "utcnow_naive", lambda: released_at)
    monkeypatch.setattr(
        order_cancellation,
        "queue_order_status",
        lambda _db, value: notifications.append(value.id),
    )

    changed = order_cancellation.cancel_order_before_settlement(
        db,
        order,
        source="provider",
    )

    assert changed is True
    assert released == [{11: 2, 12: 1}]
    assert promo.used_count == 3
    assert all(hold.status == "released" for hold in holds)
    assert all(hold.released_at == released_at for hold in holds)
    assert order.status == "cancelled"
    assert order.payment_status == "cancelled"
    assert order.delivery_status == "cancelled"
    assert notifications == [41]


def test_repeated_cancellation_is_idempotent_without_side_effects(monkeypatch):
    order = make_order(
        status="cancelled",
        payment_status="cancelled",
        delivery_status="cancelled",
    )

    class NoQuerySession:
        def query(self, _entity):
            raise AssertionError("Idempotent cancellation must not query the database")

    monkeypatch.setattr(
        order_cancellation,
        "release_variants",
        lambda *_args, **_kwargs: pytest.fail("Inventory must not be released twice"),
    )
    monkeypatch.setattr(
        order_cancellation,
        "queue_order_status",
        lambda *_args, **_kwargs: pytest.fail("Notification must not be duplicated"),
    )

    changed = order_cancellation.cancel_order_before_settlement(
        NoQuerySession(),
        order,
        source="provider",
    )

    assert changed is False


def test_manual_cancellation_rejects_existing_payment_before_mutation(monkeypatch):
    db = FakeSession(payment=SimpleNamespace(id=99))
    order = make_order(status="created", payment_status="pending")
    monkeypatch.setattr(
        order_cancellation,
        "release_variants",
        lambda *_args, **_kwargs: pytest.fail("Inventory changed before payment validation"),
    )

    with pytest.raises(HTTPException, match="Payment flow already exists") as exc_info:
        order_cancellation.cancel_order_before_settlement(
            db,
            order,
            source="manual",
        )

    assert exc_info.value.status_code == 409
    assert order.status == "created"
    assert order.payment_status == "pending"


def test_half_cancelled_order_is_rejected_as_inconsistent(monkeypatch):
    order = make_order(status="cancelled", payment_status="pending")
    monkeypatch.setattr(
        order_cancellation,
        "release_variants",
        lambda *_args, **_kwargs: pytest.fail("Inconsistent order must not mutate inventory"),
    )

    with pytest.raises(HTTPException, match="state is inconsistent") as exc_info:
        order_cancellation.cancel_order_before_settlement(
            FakeSession(),
            order,
            source="provider",
        )

    assert exc_info.value.status_code == 409
