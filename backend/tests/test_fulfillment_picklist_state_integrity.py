from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api import fulfillment as fulfillment_api
from backend.models import FulfillmentTask, FulfillmentTaskItem, OrderItem


class _Query:
    def __init__(self, db, target):
        self.db = db
        self.target = target

    def filter(self, *_args, **_kwargs):
        return self

    def with_for_update(self):
        if self.target is FulfillmentTask:
            self.db.lock_order.append("task")
        elif self.target is FulfillmentTaskItem:
            self.db.lock_order.append("task_item")
        elif self.target is OrderItem:
            self.db.lock_order.append("order_item")
        else:  # pragma: no cover - protects the test contract from silent drift
            raise AssertionError(f"unexpected locked query target: {self.target!r}")
        return self

    def scalar(self):
        if self.target is not FulfillmentTaskItem.task_id:
            raise AssertionError(f"unexpected scalar query target: {self.target!r}")
        return self.db.task_item.task_id

    def first(self):
        if self.target is FulfillmentTask:
            return self.db.task
        if self.target is FulfillmentTaskItem:
            return self.db.task_item
        if self.target is OrderItem:
            return self.db.order_item
        raise AssertionError(f"unexpected first query target: {self.target!r}")


class _Db:
    def __init__(self, task_status: str):
        self.task = SimpleNamespace(id=11, status=task_status)
        self.task_item = SimpleNamespace(
            id=22,
            task_id=11,
            order_item_id=33,
            status="to_pick",
            picked_qty=0,
            issue="",
        )
        self.order_item = SimpleNamespace(id=33, quantity=2)
        self.lock_order: list[str] = []
        self.commits = 0
        self.rollbacks = 0

    def query(self, target):
        return _Query(self, target)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _allow_write(monkeypatch):
    monkeypatch.setattr(fulfillment_api, "require_permission", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(fulfillment_api, "log_admin_action", lambda *_args, **_kwargs: None)


@pytest.mark.parametrize("task_status", ["new", "picking", "blocked"])
def test_picklist_edit_uses_parent_first_lock_order_in_editable_states(monkeypatch, task_status):
    _allow_write(monkeypatch)
    db = _Db(task_status)

    payload = fulfillment_api.update_task_item(
        22,
        picked_qty=2,
        status="picked",
        issue="",
        admin=SimpleNamespace(id=7),
        db=db,
    )

    assert db.lock_order == ["task", "task_item", "order_item"]
    assert db.commits == 1
    assert db.rollbacks == 0
    assert payload["status"] == "picked"
    assert payload["picked_qty"] == 2


@pytest.mark.parametrize("task_status", ["packed", "ready", "future_terminal"])
def test_picklist_edit_fails_closed_after_task_is_frozen(monkeypatch, task_status):
    _allow_write(monkeypatch)
    db = _Db(task_status)

    with pytest.raises(HTTPException) as exc_info:
        fulfillment_api.update_task_item(
            22,
            picked_qty=0,
            status="to_pick",
            issue="",
            admin=SimpleNamespace(id=7),
            db=db,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == (
        f"Picklist cannot be edited while fulfillment task is {task_status}"
    )
    assert db.lock_order == ["task"]
    assert db.commits == 0
    assert db.rollbacks == 1


def test_picklist_editable_state_contract_is_explicit_and_fail_closed():
    assert fulfillment_api._PICKLIST_EDITABLE_TASK_STATUSES == frozenset(
        {"new", "picking", "blocked"}
    )
