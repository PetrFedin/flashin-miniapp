from types import SimpleNamespace

import pytest

from backend.models import Notification
from backend.notification_models import NotificationEventKey
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
    def __init__(self, persisted_key=None):
        self.persisted_key = persisted_key
        self.new = []
        self.query_calls = 0

    def query(self, model):
        self.query_calls += 1
        return FakeQuery(self.persisted_key)

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


def notifications(db):
    return [value for value in db.new if isinstance(value, Notification)]


def event_keys(db):
    return [value for value in db.new if isinstance(value, NotificationEventKey)]


def test_paid_notification_is_deduplicated_before_flush():
    db = FakeSession()
    current_order = order()

    assert queue_order_paid(db, current_order) is True
    assert queue_order_paid(db, current_order) is False

    assert len(notifications(db)) == 1
    assert len(event_keys(db)) == 1
    assert event_keys(db)[0].event_key == "order:77:paid"
    assert event_keys(db)[0].notification is notifications(db)[0]
    assert db.query_calls == 1


def test_paid_notification_is_deduplicated_against_persisted_key():
    db = FakeSession(persisted_key=SimpleNamespace(id=9))

    assert queue_order_paid(db, order()) is False

    assert db.new == []
    assert db.query_calls == 1


def test_distinct_order_statuses_create_distinct_event_keys():
    db = FakeSession()
    current_order = order(status="paid")

    assert queue_order_status(db, current_order) is True
    current_order.status = "assembling"
    current_order.delivery_status = "assembling"
    assert queue_order_status(db, current_order) is True

    assert len(notifications(db)) == 2
    assert len(event_keys(db)) == 2
    assert event_keys(db)[0].event_key != event_keys(db)[1].event_key
    assert notifications(db)[0].message != notifications(db)[1].message


def test_event_keys_are_normalized_before_deduplication():
    db = FakeSession()

    assert queue_notification(
        db,
        "123456",
        "Первое сообщение",
        event_key=" ORDER:77:PAID ",
    ) is True
    assert queue_notification(
        db,
        "123456",
        "Формулировка изменилась",
        event_key="order:77:paid",
    ) is False

    assert len(notifications(db)) == 1
    assert event_keys(db)[0].event_key == "order:77:paid"


def test_generic_notifications_remain_repeatable_by_default():
    db = FakeSession(persisted_key=SimpleNamespace(id=9))

    assert queue_notification(db, "123456", "Ручное сообщение") is True
    assert queue_notification(db, "123456", "Ручное сообщение") is True

    assert len(notifications(db)) == 2
    assert event_keys(db) == []
    assert db.query_calls == 0


def test_explicit_empty_event_key_is_rejected():
    db = FakeSession()

    with pytest.raises(ValueError, match="cannot be empty"):
        queue_notification(db, "123456", "Сообщение", event_key="   ")

    assert db.new == []
