import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api import payment_reconciliation as reconciliation_api
from backend.models import Order, Payment
from backend.services import payment_reconciliation as reconciliation_service


class _Query:
    def __init__(self, value, events=None, label=""):
        self.value = value
        self.events = events
        self.label = label

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self):
        if self.events is not None:
            self.events.append(f"lock:{self.label}")
        return self

    def first(self):
        return self.value


class _EndpointSession:
    def __init__(self, initial_payment):
        self.initial_payment = initial_payment
        self.rollbacks = 0
        self.commits = 0
        self.flushes = 0
        self.refreshed = []

    def query(self, entity):
        assert entity is Payment
        return _Query(self.initial_payment)

    def rollback(self):
        self.rollbacks += 1

    def commit(self):
        self.commits += 1

    def flush(self):
        self.flushes += 1

    def refresh(self, value):
        self.refreshed.append(value)


class _LockSession:
    def __init__(self, order, payment):
        self.order = order
        self.payment = payment
        self.events = []

    def query(self, entity):
        if entity is Order:
            return _Query(self.order, self.events, "order")
        if entity is Payment:
            return _Query(self.payment, self.events, "payment")
        raise AssertionError(f"unexpected entity {entity!r}")


def _payment(*, status="pending", provider_payment_id="pay-1", order_id=7):
    return SimpleNamespace(
        id=11,
        order_id=order_id,
        provider_payment_id=provider_payment_id,
        status=status,
        amount=1250.0,
    )


def test_reconciliation_locks_order_before_payment():
    order = SimpleNamespace(id=7)
    payment = _payment()
    db = _LockSession(order, payment)

    result = reconciliation_service.lock_fresh_payment_for_reconciliation(
        db,
        11,
        expected_order_id=7,
        expected_provider_payment_id="pay-1",
    )

    assert result is payment
    assert db.events == ["lock:order", "lock:payment"]


def test_reconciliation_rejects_provider_identifier_change():
    db = _LockSession(SimpleNamespace(id=7), _payment(provider_payment_id="pay-new"))

    with pytest.raises(HTTPException) as exc_info:
        reconciliation_service.lock_fresh_payment_for_reconciliation(
            db,
            11,
            expected_order_id=7,
            expected_provider_payment_id="pay-old",
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Payment provider identifier changed during reconciliation"
    assert db.events == ["lock:order", "lock:payment"]


def test_check_payment_ends_read_transaction_and_uses_fresh_payment(monkeypatch):
    stale_payment = _payment(status="pending")
    fresh_payment = _payment(status="succeeded")
    db = _EndpointSession(stale_payment)
    admin = SimpleNamespace(id=3)
    events = []

    monkeypatch.setattr(reconciliation_api, "require_permission", lambda *_args, **_kwargs: None)

    async def fetch_provider(provider_payment_id):
        assert provider_payment_id == "pay-1"
        assert db.rollbacks == 1
        events.append("provider")
        return {
            "id": "pay-1",
            "status": "succeeded",
            "amount": {"value": "1250.00", "currency": "RUB"},
        }

    def lock_fresh(_db, payment_id, *, expected_order_id, expected_provider_payment_id):
        assert db.rollbacks == 1
        assert payment_id == 11
        assert expected_order_id == 7
        assert expected_provider_payment_id == "pay-1"
        events.append("fresh_lock")
        return fresh_payment

    row = SimpleNamespace(
        id=91,
        status="matched",
        local_status="succeeded",
        provider_status="succeeded",
    )

    def create_row(_db, payment, provider_status, provider_amount):
        assert payment is fresh_payment
        assert payment is not stale_payment
        assert provider_status == "succeeded"
        assert provider_amount == "1250.00"
        events.append("create_row")
        return row

    monkeypatch.setattr(reconciliation_api, "fetch_yookassa_payment", fetch_provider)
    monkeypatch.setattr(reconciliation_api, "lock_fresh_payment_for_reconciliation", lock_fresh)
    monkeypatch.setattr(reconciliation_api, "create_reconciliation_row", create_row)
    monkeypatch.setattr(reconciliation_api, "log_admin_action", lambda *_args, **_kwargs: None)

    result = asyncio.run(reconciliation_api.check_payment(11, admin=admin, db=db))

    assert result is row
    assert events == ["provider", "fresh_lock", "create_row"]
    assert db.rollbacks == 1
    assert db.flushes == 1
    assert db.commits == 1
    assert db.refreshed == [row]
