import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.services import fulfillment as fulfillment_service
from backend.services import fulfillment_locking as locking


def test_transition_locks_order_before_fulfillment_task(monkeypatch):
    events: list[tuple[str, int]] = []
    order = SimpleNamespace(id=101)
    task = SimpleNamespace(id=17, order_id=101)

    monkeypatch.setattr(locking, "_load_fulfillment_order_id", lambda _db, _task_id: 101)

    def lock_order(_db, order_id):
        events.append(("order", order_id))
        return order

    def lock_task(_db, task_id):
        events.append(("task", task_id))
        return task

    monkeypatch.setattr(locking, "_select_fulfillment_order_for_update", lock_order)
    monkeypatch.setattr(locking, "_select_fulfillment_task_for_update", lock_task)

    locked_order, locked_task = locking.lock_fulfillment_task_for_update(object(), 17)

    assert locked_order is order
    assert locked_task is task
    assert events == [("order", 101), ("task", 17)]


def test_missing_task_snapshot_takes_no_root_lock(monkeypatch):
    monkeypatch.setattr(locking, "_load_fulfillment_order_id", lambda _db, _task_id: None)
    monkeypatch.setattr(
        locking,
        "_select_fulfillment_order_for_update",
        lambda *_args, **_kwargs: pytest.fail("missing task must not lock an order"),
    )
    monkeypatch.setattr(
        locking,
        "_select_fulfillment_task_for_update",
        lambda *_args, **_kwargs: pytest.fail("missing task must not be locked"),
    )

    with pytest.raises(HTTPException) as exc_info:
        locking.lock_fulfillment_task_for_update(object(), 17)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Fulfillment task not found"


def test_missing_order_does_not_lock_fulfillment_task(monkeypatch):
    monkeypatch.setattr(locking, "_load_fulfillment_order_id", lambda _db, _task_id: 101)
    monkeypatch.setattr(locking, "_select_fulfillment_order_for_update", lambda _db, _order_id: None)
    monkeypatch.setattr(
        locking,
        "_select_fulfillment_task_for_update",
        lambda *_args, **_kwargs: pytest.fail("task must not be locked before missing order is rejected"),
    )

    with pytest.raises(HTTPException) as exc_info:
        locking.lock_fulfillment_task_for_update(object(), 17)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Fulfillment task is linked to a missing order"


def test_task_disappears_after_snapshot_fails_closed(monkeypatch):
    monkeypatch.setattr(locking, "_load_fulfillment_order_id", lambda _db, _task_id: 101)
    monkeypatch.setattr(
        locking,
        "_select_fulfillment_order_for_update",
        lambda _db, _order_id: SimpleNamespace(id=101),
    )
    monkeypatch.setattr(locking, "_select_fulfillment_task_for_update", lambda _db, _task_id: None)

    with pytest.raises(HTTPException) as exc_info:
        locking.lock_fulfillment_task_for_update(object(), 17)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Fulfillment task changed while being locked"


def test_task_order_change_fails_closed(monkeypatch):
    monkeypatch.setattr(locking, "_load_fulfillment_order_id", lambda _db, _task_id: 101)
    monkeypatch.setattr(
        locking,
        "_select_fulfillment_order_for_update",
        lambda _db, _order_id: SimpleNamespace(id=101),
    )
    monkeypatch.setattr(
        locking,
        "_select_fulfillment_task_for_update",
        lambda _db, _task_id: SimpleNamespace(id=17, order_id=202),
    )

    with pytest.raises(HTTPException) as exc_info:
        locking.lock_fulfillment_task_for_update(object(), 17)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Fulfillment task changed while being locked"


def test_transition_service_rejects_mismatched_prelocked_root(monkeypatch):
    monkeypatch.setattr(
        fulfillment_service,
        "queue_order_status",
        lambda *_args, **_kwargs: pytest.fail("mismatched root must fail before side effects"),
    )
    order = SimpleNamespace(id=101, payment_status="paid", status="paid", delivery_status="not_started")
    task = SimpleNamespace(id=17, order_id=202, status="new", comment="")

    with pytest.raises(ValueError, match="Fulfillment task changed while being updated"):
        fulfillment_service.update_fulfillment_status(
            object(),
            order,
            task,
            "blocked",
            "warehouse blocked",
        )


def test_transition_service_consumes_prelocked_order_without_requery(monkeypatch):
    class NoQueryDb:
        def query(self, *_args, **_kwargs):
            raise AssertionError("transition service must not reacquire Order after FulfillmentTask")

    queued: list[int] = []
    monkeypatch.setattr(
        fulfillment_service,
        "queue_order_status",
        lambda _db, order: queued.append(order.id),
    )
    order = SimpleNamespace(
        id=101,
        payment_status="paid",
        status="paid",
        delivery_status="not_started",
    )
    task = SimpleNamespace(
        id=17,
        order_id=101,
        status="new",
        comment="",
    )

    fulfillment_service.update_fulfillment_status(
        NoQueryDb(),
        order,
        task,
        "blocked",
        "warehouse blocked",
    )

    assert task.status == "blocked"
    assert task.comment == "warehouse blocked"
    assert order.status == "assembling"
    assert order.delivery_status == "assembling"
    assert queued == [101]


@pytest.mark.skipif(
    not os.getenv("DATABASE_URL", "").startswith("postgresql"),
    reason="requires PostgreSQL row-lock semantics",
)
def test_postgres_fulfillment_lock_order_smoke():
    from scripts.fulfillment_lock_order_smoke import main

    assert main() == 0
