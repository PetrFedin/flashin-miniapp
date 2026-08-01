from types import SimpleNamespace

from backend.models import Notification
from backend.services.notifications import (
    queue_notification,
    queue_order_paid,
    queue_order_status,
)


class FakeQuery:
    def __init__(self, value):
        self.value = value

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.value


class FakeSession:
    def __init__(self, persisted=None):
        self.persisted = persisted
        self.new = []
        self.query_calls = 0

    def query(self, model):
        self.query_calls += 1
        return FakeQuery(self.persisted)

    def add(self, value):
        self.new.append(value)


def order(*, status="paid", delivery_status="not_started"):
    return SimpleNamespace(
        id=77,
        status=status,
        payment_status="paid",
        delivery_status=delivery_status,
        total_amount=12500,
        currency="RUB",
        customer=SimpleNamespace(telegram_id="123456"),
    )


def test_paid_notification_is_deduplicated_before_flush():
    db = FakeSession()
    current_order = order()

    assert queue_order_paid(db, current_order) is True
    assert queue_order_paid(db, current_order) is False

    assert len(db.new) == 1
    assert isinstance(db.new[0], Notification)
    assert db.query_calls == 1


def test_paid_notification_is_deduplicated_against_persisted_queue():
    db = FakeSession(persisted=SimpleNamespace(id=9))

    assert queue_order_paid(db, order()) is False

    assert db.new == []
    assert db.query_calls == 1


def test_distinct_order_statuses_create_distinct_notifications():
    db = FakeSession()
    current_order = order(status="paid")

    assert queue_order_status(db, current_order) is True
    current_order.status = "assembling"
    current_order.delivery_status = "assembling"
    assert queue_order_status(db, current_order) is True

    assert len(db.new) == 2
    assert db.new[0].message != db.new[1].message


def test_generic_notifications_remain_repeatable_by_default():
    db = FakeSession(persisted=SimpleNamespace(id=9))

    assert queue_notification(db, "123456", "Ручное сообщение") is True
    assert queue_notification(db, "123456", "Ручное сообщение") is True

    assert len(db.new) == 2
    assert db.query_calls == 0
