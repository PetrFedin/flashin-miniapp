import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.services import payment_creation as creation


def test_finalize_rows_lock_order_before_attempt(monkeypatch):
    events: list[tuple[str, int]] = []
    order = SimpleNamespace(id=101)
    attempt = SimpleNamespace(id=17, order_id=101)

    monkeypatch.setattr(
        creation,
        "_payment_creation_attempt_order_id",
        lambda _db, attempt_id: 101 if attempt_id == 17 else pytest.fail("unexpected attempt"),
    )

    def lock_order(_db, order_id):
        events.append(("order", order_id))
        return order

    def lock_attempt(_db, attempt_id):
        events.append(("attempt", attempt_id))
        return attempt

    monkeypatch.setattr(creation, "_lock_payment_creation_order", lock_order)
    monkeypatch.setattr(creation, "_lock_payment_creation_attempt", lock_attempt)

    locked_order, locked_attempt = creation._lock_finalize_order_then_attempt(object(), 17)

    assert locked_order is order
    assert locked_attempt is attempt
    assert events == [("order", 101), ("attempt", 17)]


def test_finalize_rows_do_not_lock_attempt_when_order_is_missing(monkeypatch):
    monkeypatch.setattr(creation, "_payment_creation_attempt_order_id", lambda _db, _attempt_id: 101)

    def missing_order(_db, _order_id):
        raise HTTPException(status_code=409, detail="Payment order is missing")

    monkeypatch.setattr(creation, "_lock_payment_creation_order", missing_order)
    monkeypatch.setattr(
        creation,
        "_lock_payment_creation_attempt",
        lambda *_args, **_kwargs: pytest.fail("attempt must not be locked after missing order"),
    )

    with pytest.raises(HTTPException) as exc_info:
        creation._lock_finalize_order_then_attempt(object(), 17)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Payment order is missing"


def test_finalize_rows_reject_attempt_order_change(monkeypatch):
    order = SimpleNamespace(id=101)
    attempt = SimpleNamespace(id=17, order_id=202)

    monkeypatch.setattr(creation, "_payment_creation_attempt_order_id", lambda _db, _attempt_id: 101)
    monkeypatch.setattr(creation, "_lock_payment_creation_order", lambda _db, _order_id: order)
    monkeypatch.setattr(creation, "_lock_payment_creation_attempt", lambda _db, _attempt_id: attempt)

    with pytest.raises(HTTPException) as exc_info:
        creation._lock_finalize_order_then_attempt(object(), 17)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Payment creation attempt changed during finalization"


@pytest.mark.skipif(
    not os.getenv("DATABASE_URL", "").startswith("postgresql"),
    reason="requires PostgreSQL row-lock semantics",
)
def test_postgres_payment_creation_finalize_lock_order_smoke():
    from scripts.payment_creation_finalize_lock_order_smoke import main

    assert main() == 0
