import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.services import refund_locking as locking


def test_approval_locks_order_before_return_request(monkeypatch):
    events: list[tuple[str, object]] = []
    order = SimpleNamespace(id=101)
    ret = SimpleNamespace(id=17, order_id=101)

    monkeypatch.setattr(locking, "_load_return_request_order_id", lambda _db, _return_id: 101)

    def lock_order(_db, order_id):
        events.append(("order", order_id))
        return order

    def lock_return(_db, return_id):
        events.append(("return", return_id))
        return ret

    monkeypatch.setattr(locking, "_select_refund_order_for_update", lock_order)
    monkeypatch.setattr(locking, "_select_return_request_for_update", lock_return)

    locked_order, locked_return = locking.lock_return_request_for_approval(object(), 17)

    assert locked_order is order
    assert locked_return is ret
    assert events == [("order", 101), ("return", 17)]


def test_approval_missing_order_does_not_lock_return_request(monkeypatch):
    monkeypatch.setattr(locking, "_load_return_request_order_id", lambda _db, _return_id: 101)
    monkeypatch.setattr(locking, "_select_refund_order_for_update", lambda _db, _order_id: None)
    monkeypatch.setattr(
        locking,
        "_select_return_request_for_update",
        lambda *_args, **_kwargs: pytest.fail("return must not be locked before a missing order is rejected"),
    )

    with pytest.raises(HTTPException) as exc_info:
        locking.lock_return_request_for_approval(object(), 17)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Order not found"


def test_approval_rejects_return_order_change(monkeypatch):
    monkeypatch.setattr(locking, "_load_return_request_order_id", lambda _db, _return_id: 101)
    monkeypatch.setattr(
        locking,
        "_select_refund_order_for_update",
        lambda _db, _order_id: SimpleNamespace(id=101),
    )
    monkeypatch.setattr(
        locking,
        "_select_return_request_for_update",
        lambda _db, _return_id: SimpleNamespace(id=17, order_id=202),
    )

    with pytest.raises(HTTPException) as exc_info:
        locking.lock_return_request_for_approval(object(), 17)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Return request changed during refund locking"


def test_known_order_recovery_locks_order_before_return_request(monkeypatch):
    events: list[tuple[str, int]] = []
    order = SimpleNamespace(id=101)
    ret = SimpleNamespace(id=17, order_id=101)

    def lock_order(_db, order_id):
        events.append(("order", order_id))
        return order

    def lock_return(_db, return_id):
        events.append(("return", return_id))
        return ret

    monkeypatch.setattr(locking, "_select_refund_order_for_update", lock_order)
    monkeypatch.setattr(locking, "_select_return_request_for_update", lock_return)

    locked_order, locked_return = locking.lock_return_request_for_known_order(object(), 17, 101)

    assert locked_order is order
    assert locked_return is ret
    assert events == [("order", 101), ("return", 17)]


def test_provider_refund_locks_order_before_return_request(monkeypatch):
    events: list[tuple[str, object]] = []
    order = SimpleNamespace(id=101)
    ret = SimpleNamespace(id=17, order_id=101, provider_refund_id="refund-17")

    monkeypatch.setattr(
        locking,
        "_load_provider_refund_order_id",
        lambda _db, provider_refund_id: 101
        if provider_refund_id == "refund-17"
        else pytest.fail("unexpected provider refund id"),
    )

    def lock_order(_db, order_id):
        events.append(("order", order_id))
        return order

    def lock_return(_db, provider_refund_id):
        events.append(("return", provider_refund_id))
        return ret

    monkeypatch.setattr(locking, "_select_refund_order_for_update", lock_order)
    monkeypatch.setattr(locking, "_select_provider_return_request_for_update", lock_return)

    locked_order, locked_return = locking.lock_return_request_for_provider_refund(
        object(),
        "refund-17",
    )

    assert locked_order is order
    assert locked_return is ret
    assert events == [("order", 101), ("return", "refund-17")]


def test_provider_refund_rejects_binding_change(monkeypatch):
    monkeypatch.setattr(locking, "_load_provider_refund_order_id", lambda _db, _refund_id: 101)
    monkeypatch.setattr(
        locking,
        "_select_refund_order_for_update",
        lambda _db, _order_id: SimpleNamespace(id=101),
    )
    monkeypatch.setattr(
        locking,
        "_select_provider_return_request_for_update",
        lambda _db, _refund_id: SimpleNamespace(id=17, order_id=202),
    )

    with pytest.raises(HTTPException) as exc_info:
        locking.lock_return_request_for_provider_refund(object(), "refund-17")

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Provider refund binding changed during refund locking"


@pytest.mark.skipif(
    not os.getenv("DATABASE_URL", "").startswith("postgresql"),
    reason="requires PostgreSQL row-lock semantics",
)
def test_postgres_refund_return_lock_order_smoke():
    from scripts.refund_return_lock_order_smoke import main

    assert main() == 0
