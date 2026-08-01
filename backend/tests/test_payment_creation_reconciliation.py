import asyncio
from types import SimpleNamespace

from backend.api import payments as payments_api
from backend.models import Order, Payment
from backend.schemas import PaymentCreate


class FakeQuery:
    def __init__(self, session, entity):
        self.session = session
        self.entity = entity

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def with_for_update(self):
        return self

    def first(self):
        if self.entity is Order:
            return self.session.order
        if self.entity is Payment:
            return self.session.payments[-1] if self.session.payments else None
        return None

    def scalar(self):
        return len(self.session.payments)


class FakeSession:
    def __init__(self, order, payments):
        self.order = order
        self.payments = list(payments)
        self.commits = 0
        self.rollbacks = 0

    def query(self, entity):
        return FakeQuery(self, entity)

    def add(self, value):
        if isinstance(value, Payment):
            self.payments.append(value)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def make_order():
    return SimpleNamespace(
        id=7,
        customer_id=3,
        status="payment_created",
        payment_status="payment_created",
        total_amount=1250.0,
        currency="RUB",
    )


def make_payment(status="pending", url="https://pay.example/old"):
    return Payment(
        id=1,
        order_id=7,
        provider="yookassa",
        provider_payment_id="pay-1",
        status=status,
        amount=1250.0,
        confirmation_url=url,
    )


def provider_snapshot(status, payment_id="pay-1", url=""):
    result = {
        "id": payment_id,
        "status": status,
        "metadata": {"order_id": "7"},
        "amount": {"value": "1250.00", "currency": "RUB"},
    }
    if url:
        result["confirmation"] = {"confirmation_url": url}
    return result


def test_canceled_attempt_creates_next_provider_attempt(monkeypatch):
    order = make_order()
    old_payment = make_payment()
    db = FakeSession(order, [old_payment])
    create_calls = []

    async def fetch(_payment_id):
        return provider_snapshot("canceled")

    async def create(order_id, amount, currency, attempt):
        create_calls.append((order_id, amount, currency, attempt))
        return {
            "provider_payment_id": "pay-2",
            "status": "pending",
            "confirmation_url": "https://pay.example/new",
        }

    monkeypatch.setattr(payments_api, "fetch_yookassa_payment", fetch)
    monkeypatch.setattr(payments_api, "create_yookassa_payment", create)

    result = asyncio.run(
        payments_api.create_payment(
            PaymentCreate(order_id=7),
            customer=SimpleNamespace(id=3),
            db=db,
        )
    )

    assert old_payment.status == "canceled"
    assert old_payment.confirmation_url == ""
    assert create_calls == [(7, 1250.0, "RUB", 2)]
    assert len(db.payments) == 2
    assert db.payments[-1].provider_payment_id == "pay-2"
    assert result.provider_payment_id == "pay-2"
    assert result.confirmation_url == "https://pay.example/new"
    assert db.commits == 1
    assert db.rollbacks == 0


def test_succeeded_attempt_is_reused_without_second_charge(monkeypatch):
    order = make_order()
    payment = make_payment()
    db = FakeSession(order, [payment])

    async def fetch(_payment_id):
        return provider_snapshot("succeeded")

    async def create(*args, **kwargs):
        raise AssertionError("succeeded payment must not create a second charge")

    monkeypatch.setattr(payments_api, "fetch_yookassa_payment", fetch)
    monkeypatch.setattr(payments_api, "create_yookassa_payment", create)

    result = asyncio.run(
        payments_api.create_payment(
            PaymentCreate(order_id=7),
            customer=SimpleNamespace(id=3),
            db=db,
        )
    )

    assert result.status == "succeeded"
    assert result.confirmation_url == ""
    assert len(db.payments) == 1
    assert db.commits == 1
    assert db.rollbacks == 0


def test_pending_attempt_refreshes_link_without_new_charge(monkeypatch):
    order = make_order()
    payment = make_payment()
    db = FakeSession(order, [payment])

    async def fetch(_payment_id):
        return provider_snapshot("pending", url="https://pay.example/refreshed")

    async def create(*args, **kwargs):
        raise AssertionError("active payment must be reused")

    monkeypatch.setattr(payments_api, "fetch_yookassa_payment", fetch)
    monkeypatch.setattr(payments_api, "create_yookassa_payment", create)

    result = asyncio.run(
        payments_api.create_payment(
            PaymentCreate(order_id=7),
            customer=SimpleNamespace(id=3),
            db=db,
        )
    )

    assert result.status == "pending"
    assert result.confirmation_url == "https://pay.example/refreshed"
    assert len(db.payments) == 1
    assert db.commits == 1
