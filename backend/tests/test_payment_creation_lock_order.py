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


def test_review_required_locks_order_before_attempt(monkeypatch):
    events: list[tuple[str, int]] = []
    attempt = SimpleNamespace(
        id=17,
        order_id=101,
        status="creating",
        lease_expires_at=object(),
        last_error="",
        provider_payment_id="",
        updated_at=None,
    )

    monkeypatch.setattr(
        creation,
        "_load_payment_creation_attempt_order_id",
        lambda _db, attempt_id: 101 if attempt_id == 17 else pytest.fail("unexpected attempt"),
    )

    def select_order(_db, order_id):
        events.append(("order", order_id))
        return SimpleNamespace(id=order_id)

    def select_attempt(_db, attempt_id):
        events.append(("attempt", attempt_id))
        return attempt

    monkeypatch.setattr(creation, "_select_payment_creation_order_for_update", select_order)
    monkeypatch.setattr(creation, "_select_payment_creation_attempt_for_update", select_attempt)

    creation.mark_payment_creation_review_required(
        object(),
        17,
        "provider_integrity_failed",
        provider_payment_id="pay_17",
    )

    assert events == [("order", 101), ("attempt", 17)]
    assert attempt.status == "review_required"
    assert attempt.lease_expires_at is None
    assert attempt.last_error == "provider_integrity_failed"
    assert attempt.provider_payment_id == "pay_17"
    assert attempt.updated_at is not None


def test_review_required_missing_attempt_keeps_noop_semantics(monkeypatch):
    monkeypatch.setattr(
        creation,
        "_load_payment_creation_attempt_order_id",
        lambda _db, _attempt_id: None,
    )
    monkeypatch.setattr(
        creation,
        "_select_payment_creation_order_for_update",
        lambda *_args, **_kwargs: pytest.fail("missing attempt must not lock order"),
    )
    monkeypatch.setattr(
        creation,
        "_select_payment_creation_attempt_for_update",
        lambda *_args, **_kwargs: pytest.fail("missing attempt must not lock attempt"),
    )

    assert creation.mark_payment_creation_review_required(object(), 17, "missing") is None


@pytest.mark.skipif(
    not os.getenv("DATABASE_URL", "").startswith("postgresql"),
    reason="requires PostgreSQL row-lock semantics",
)
def test_postgres_payment_creation_finalize_lock_order_smoke():
    from scripts.payment_creation_finalize_lock_order_smoke import main

    assert main() == 0
