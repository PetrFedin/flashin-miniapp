import asyncio
from types import SimpleNamespace

from backend.api import payments as payments_api
from backend.models import Order, Payment
from backend.payment_attempt_models import PaymentCreationAttempt
from backend.schemas import PaymentCreate


class FakeQuery:
    def __init__(self, session, entity):
        self.session = session
        self.entity = entity
        self.filters = []

    def filter(self, *args, **kwargs):
        for expression in args:
            left = getattr(expression, "left", None)
            right = getattr(expression, "right", None)
            key = getattr(left, "key", None)
            operator = getattr(getattr(expression, "operator", None), "__name__", "")
            value = getattr(right, "value", None)
            if key and operator in {"eq", "in_op"}:
                self.filters.append((key, operator, value))
        return self

    def order_by(self, *args, **kwargs):
        return self

    def with_for_update(self):
        return self

    def _matches(self, value):
        for key, operator, expected in self.filters:
            if not hasattr(value, key):
                continue
            actual = getattr(value, key)
            if operator == "eq" and actual != expected:
                return False
            if operator == "in_op" and actual not in expected:
                return False
        return True

    def first(self):
        if self.entity is Order:
            return self.session.order if self._matches(self.session.order) else None
        if self.entity is Payment:
            matches = [value for value in self.session.payments if self._matches(value)]
            return matches[-1] if matches else None
        if self.entity is PaymentCreationAttempt:
            matches = [value for value in self.session.attempts if self._matches(value)]
            return matches[-1] if matches else None
        return None

    def scalar(self):
        function_name = str(getattr(self.entity, "name", ""))
        if function_name == "max":
            values = [
                value.attempt_number
                for value in self.session.attempts
                if self._matches(value)
            ]
            return max(values) if values else 0
        if function_name == "count":
            return len([value for value in self.session.payments if self._matches(value)])
        return 0


class FakeSession:
    def __init__(self, order, payments):
        self.order = order
        self.payments = list(payments)
        self.attempts = []
        self.commits = 0
        self.rollbacks = 0
        self.flushes = 0

    def query(self, entity):
        return FakeQuery(self, entity)

    def add(self, value):
        if isinstance(value, Payment):
            if value.id is None:
                value.id = max([payment.id or 0 for payment in self.payments] or [0]) + 1
            self.payments.append(value)
        elif isinstance(value, PaymentCreationAttempt):
            if value.id is None:
                value.id = max([attempt.id or 0 for attempt in self.attempts] or [0]) + 1
            self.attempts.append(value)

    def flush(self):
        self.flushes += 1

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def make_order(**overrides):
    values = {
        "id": 7,
        "customer_id": 3,
        "status": "payment_created",
        "payment_status": "payment_created",
        "total_amount": 1250.0,
        "currency": "RUB",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


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


def settlement_spy(monkeypatch):
    calls = []

    def settle(db, order):
        calls.append((db, order.id))
        order.status = "paid"
        order.payment_status = "paid"
        return True

    monkeypatch.setattr(payments_api, "settle_paid_order", settle)
    return calls


def test_canceled_attempt_creates_next_provider_attempt(monkeypatch):
    order = make_order()
    old_payment = make_payment()
    db = FakeSession(order, [old_payment])
    create_calls = []

    async def fetch(payment_id):
        if payment_id == "pay-1":
            return provider_snapshot("canceled", payment_id="pay-1")
        if payment_id == "pay-2":
            return provider_snapshot(
                "pending",
                payment_id="pay-2",
                url="https://pay.example/new",
            )
        raise AssertionError(f"unexpected provider payment id: {payment_id}")

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
    assert db.attempts[-1].attempt_number == 2
    assert db.attempts[-1].status == "completed"
    assert result.provider_payment_id == "pay-2"
    assert result.confirmation_url == "https://pay.example/new"
    assert db.commits == 4
    assert db.rollbacks == 0


def test_succeeded_attempt_self_heals_order_without_second_charge(monkeypatch):
    order = make_order()
    payment = make_payment()
    db = FakeSession(order, [payment])
    settle_calls = settlement_spy(monkeypatch)

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
    assert order.status == "paid"
    assert order.payment_status == "paid"
    assert settle_calls == [(db, 7)]
    assert len(db.payments) == 1
    assert db.commits == 2
    assert db.rollbacks == 0


def test_immediately_succeeded_new_attempt_is_settled(monkeypatch):
    order = make_order(status="created", payment_status="pending")
    db = FakeSession(order, [])
    settle_calls = settlement_spy(monkeypatch)

    async def create(order_id, amount, currency, attempt):
        assert (order_id, amount, currency, attempt) == (7, 1250.0, "RUB", 1)
        return {
            "provider_payment_id": "pay-immediate",
            "status": "succeeded",
            "confirmation_url": "",
        }

    async def fetch(payment_id):
        assert payment_id == "pay-immediate"
        return provider_snapshot("succeeded", payment_id="pay-immediate")

    monkeypatch.setattr(payments_api, "create_yookassa_payment", create)
    monkeypatch.setattr(payments_api, "fetch_yookassa_payment", fetch)

    result = asyncio.run(
        payments_api.create_payment(
            PaymentCreate(order_id=7),
            customer=SimpleNamespace(id=3),
            db=db,
        )
    )

    assert result.status == "succeeded"
    assert result.provider_payment_id == "pay-immediate"
    assert order.status == "paid"
    assert order.payment_status == "paid"
    assert settle_calls == [(db, 7)]
    assert len(db.payments) == 1
    assert db.attempts[-1].status == "completed"
    assert db.commits == 2
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
    assert db.commits == 2
    assert db.rollbacks == 0
