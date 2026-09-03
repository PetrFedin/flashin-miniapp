from types import SimpleNamespace

import pytest

from backend.api import returns as returns_api
from backend.models import Order, ReturnRequest


class _Query:
    def __init__(self, value):
        self.value = value

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self.value


class _Db:
    def __init__(self, ret, order):
        self.ret = ret
        self.order = order
        self.commits = 0
        self.rollbacks = 0

    def query(self, model):
        if model is ReturnRequest:
            return _Query(self.ret)
        if model is Order:
            return _Query(self.order)
        raise AssertionError(f"Unexpected model query: {model}")

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _return(status="processing", provider_refund_id="", order_id=202):
    return SimpleNamespace(
        id=101,
        order_id=order_id,
        status=status,
        provider_refund_id=provider_refund_id,
        refund_amount=250.0,
    )


def _order(status="refund_requested", payment_status="refund_processing"):
    return SimpleNamespace(id=202, status=status, payment_status=payment_status)


@pytest.mark.parametrize(
    ("return_status", "order_status", "payment_status"),
    [
        ("approved", "refunded", "refunded"),
        ("approved_partial", "partially_refunded", "partially_refunded"),
    ],
)
def test_stale_review_cannot_demote_terminal_refund_or_stop_pilot(
    monkeypatch,
    return_status,
    order_status,
    payment_status,
):
    ret = _return(return_status, "refund-final")
    order = _order(order_status, payment_status)
    db = _Db(ret, order)
    pilot_calls = []

    monkeypatch.setattr(
        returns_api,
        "stop_pilot_for_order",
        lambda *args, **kwargs: pilot_calls.append((args, kwargs)),
    )

    response = returns_api._mark_review_required(
        db,
        ret.id,
        order.id,
        "refund-stale",
    )

    assert response == {
        "ok": True,
        "refund_id": "refund-final",
        "status": "succeeded",
        "return_status": return_status,
        "refund_amount": 250.0,
        "idempotent": True,
    }
    assert ret.status == return_status
    assert ret.provider_refund_id == "refund-final"
    assert order.status == order_status
    assert order.payment_status == payment_status
    assert pilot_calls == []
    assert db.commits == 0
    assert db.rollbacks == 1


@pytest.mark.parametrize(
    ("return_status", "order_status", "payment_status"),
    [
        ("approved", "refunded", "refunded"),
        ("approved_partial", "partially_refunded", "partially_refunded"),
    ],
)
def test_stale_provider_error_cannot_demote_terminal_refund_or_stop_pilot(
    monkeypatch,
    return_status,
    order_status,
    payment_status,
):
    ret = _return(return_status, "refund-final")
    order = _order(order_status, payment_status)
    db = _Db(ret, order)
    pilot_calls = []

    monkeypatch.setattr(
        returns_api,
        "stop_pilot_for_order",
        lambda *args, **kwargs: pilot_calls.append((args, kwargs)),
    )

    response = returns_api._mark_retry_required(db, ret.id, order.id)

    assert response is not None
    assert response["idempotent"] is True
    assert response["return_status"] == return_status
    assert ret.status == return_status
    assert order.status == order_status
    assert order.payment_status == payment_status
    assert pilot_calls == []
    assert db.commits == 0
    assert db.rollbacks == 1


def test_nonterminal_review_still_persists_review_and_stops_pilot(monkeypatch):
    ret = _return("processing", "")
    order = _order("refund_requested", "refund_processing")
    db = _Db(ret, order)
    pilot_calls = []

    def fake_stop(db_arg, *, order_id, reason):
        pilot_calls.append((db_arg, order_id, reason))

    monkeypatch.setattr(returns_api, "stop_pilot_for_order", fake_stop)

    response = returns_api._mark_review_required(
        db,
        ret.id,
        order.id,
        "refund-provider",
    )

    assert response is None
    assert ret.status == "refund_review_required"
    assert ret.provider_refund_id == "refund-provider"
    assert order.status == "refund_requested"
    assert order.payment_status == "refund_review_required"
    assert pilot_calls == [(db, order.id, "refund_review_required")]
    assert db.commits == 1
    assert db.rollbacks == 0


def test_nonterminal_provider_error_still_persists_retry_and_stops_pilot(monkeypatch):
    ret = _return("processing", "refund-provider")
    order = _order("refund_requested", "refund_processing")
    db = _Db(ret, order)
    pilot_calls = []

    def fake_stop(db_arg, *, order_id, reason):
        pilot_calls.append((db_arg, order_id, reason))

    monkeypatch.setattr(returns_api, "stop_pilot_for_order", fake_stop)

    response = returns_api._mark_retry_required(db, ret.id, order.id)

    assert response is None
    assert ret.status == "refund_retry_required"
    assert order.status == "refund_requested"
    assert order.payment_status == "refund_pending"
    assert pilot_calls == [(db, order.id, "refund_retry_required")]
    assert db.commits == 1
    assert db.rollbacks == 0
