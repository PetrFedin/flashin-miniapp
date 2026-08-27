from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api.support import create_ticket
from backend.models import Order


class _OrderQuery:
    def __init__(self, result):
        self.result = result
        self.criteria = ()

    def filter(self, *criteria):
        self.criteria = criteria
        return self

    def first(self):
        return self.result


class _Db:
    def __init__(self, order_result=None):
        self.order_query = _OrderQuery(order_result)
        self.query_calls = []
        self.added = []
        self.commits = 0
        self.refreshes = 0

    def query(self, model):
        self.query_calls.append(model)
        assert model is Order
        return self.order_query

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1

    def refresh(self, _obj):
        self.refreshes += 1


def _payload(order_id):
    return SimpleNamespace(
        order_id=order_id,
        subject="Order question",
        message="Please help with this order.",
        priority="normal",
    )


def test_create_ticket_without_order_does_not_require_order_lookup():
    db = _Db()
    customer = SimpleNamespace(id=7)

    ticket = create_ticket(_payload(None), customer=customer, db=db)

    assert db.query_calls == []
    assert ticket.customer_id == 7
    assert ticket.order_id is None
    assert db.added == [ticket]
    assert db.commits == 1
    assert db.refreshes == 1


def test_create_ticket_accepts_only_order_owned_by_customer():
    db = _Db(order_result=SimpleNamespace(id=101, customer_id=7))
    customer = SimpleNamespace(id=7)

    ticket = create_ticket(_payload(101), customer=customer, db=db)

    assert db.query_calls == [Order]
    assert db.added == [ticket]
    assert db.commits == 1
    assert db.refreshes == 1

    column_keys = {
        getattr(getattr(criteria, "left", None), "key", None)
        for criteria in db.order_query.criteria
    }
    right_values = {
        getattr(getattr(criteria, "right", None), "value", None)
        for criteria in db.order_query.criteria
    }
    assert {"id", "customer_id"} <= column_keys
    assert {101, 7} <= right_values


@pytest.mark.parametrize("order_id", [101, 999999])
def test_create_ticket_hides_foreign_or_missing_order_and_has_no_side_effects(order_id):
    db = _Db(order_result=None)
    customer = SimpleNamespace(id=7)

    with pytest.raises(HTTPException) as exc_info:
        create_ticket(_payload(order_id), customer=customer, db=db)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Order not found"
    assert db.query_calls == [Order]
    assert db.added == []
    assert db.commits == 0
    assert db.refreshes == 0
