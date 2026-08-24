from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from backend.api import order_cancellation
from backend.main import app
from backend.schemas import OrderStatusUpdate


ROOT = Path(__file__).resolve().parents[1]


class FakeSession:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _admin():
    return SimpleNamespace(id=7, email="ops@flashin.test", role="manager")


def _paid_order(**overrides):
    values = {
        "id": 17,
        "status": "paid",
        "payment_status": "paid",
        "delivery_status": "not_started",
        "tracking_number": "",
        "items": [SimpleNamespace(id=1, quantity=1)],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _task(**overrides):
    values = {
        "id": 31,
        "order_id": 17,
        "status": "new",
        "assigned_admin_id": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _prepare(monkeypatch, order, task):
    permissions: list[str] = []
    audits: list[tuple] = []
    transitions: list[str] = []

    monkeypatch.setattr(
        order_cancellation,
        "require_permission",
        lambda _db, _admin, permission: permissions.append(permission),
    )
    monkeypatch.setattr(order_cancellation, "_load_locked_order", lambda _db, _id: order)
    monkeypatch.setattr(
        order_cancellation,
        "_load_locked_fulfillment_task",
        lambda _db, _id: task,
    )
    monkeypatch.setattr(order_cancellation, "_load_order_response", lambda _db, _id: order)
    monkeypatch.setattr(
        order_cancellation,
        "log_admin_action",
        lambda *args: audits.append(args),
    )

    def transition(_db, value, target_status):
        transitions.append(target_status)
        assert target_status == "picking"
        value.status = "picking"
        order.status = "assembling"
        order.delivery_status = "assembling"

    monkeypatch.setattr(order_cancellation, "update_fulfillment_status", transition)
    return permissions, audits, transitions


def test_paid_to_assembling_delegates_to_authoritative_fulfillment_service(monkeypatch):
    order = _paid_order()
    task = _task()
    db = FakeSession()
    permissions, audits, transitions = _prepare(monkeypatch, order, task)

    result = order_cancellation.start_admin_fulfillment_via_generic_patch(
        order.id,
        OrderStatusUpdate(status=" ASSEMBLING "),
        admin=_admin(),
        db=db,
    )

    assert result is order
    assert permissions == ["orders.write", "fulfillment.write"]
    assert transitions == ["picking"]
    assert task.assigned_admin_id == 7
    assert order.status == "assembling"
    assert order.delivery_status == "assembling"
    assert db.commits == 1
    assert db.rollbacks == 0
    assert len(audits) == 1
    assert audits[0][2] == "fulfillment.task.update"
    assert audits[0][-1] == {
        "from_status": "new",
        "status": "picking",
        "assigned_admin_id": 7,
        "source": "admin_order_gateway",
    }


def test_missing_paid_fulfillment_task_is_recovered_through_shared_creator(monkeypatch):
    order = _paid_order()
    task = _task()
    db = FakeSession()
    permissions, audits, transitions = _prepare(monkeypatch, order, None)
    created: list[int] = []

    def ensure(_db, value):
        created.append(value.id)
        return task

    monkeypatch.setattr(order_cancellation, "ensure_fulfillment_task", ensure)

    order_cancellation.start_admin_fulfillment_via_generic_patch(
        order.id,
        OrderStatusUpdate(status="assembling"),
        admin=_admin(),
        db=db,
    )

    assert created == [17]
    assert permissions == ["orders.write", "fulfillment.write"]
    assert transitions == ["picking"]
    assert len(audits) == 1
    assert db.commits == 1


def test_assembling_retry_is_idempotent_when_fulfillment_already_started(monkeypatch):
    order = _paid_order(status="assembling", delivery_status="assembling")
    task = _task(status="picking", assigned_admin_id=7)
    db = FakeSession()
    permissions, audits, transitions = _prepare(monkeypatch, order, task)

    result = order_cancellation.start_admin_fulfillment_via_generic_patch(
        order.id,
        OrderStatusUpdate(status="assembling"),
        admin=_admin(),
        db=db,
    )

    assert result is order
    assert permissions == ["orders.write", "fulfillment.write"]
    assert transitions == []
    assert audits == []
    assert db.commits == 0
    assert db.rollbacks == 1


@pytest.mark.parametrize(
    "payload",
    [
        OrderStatusUpdate(status="ready"),
        OrderStatusUpdate(status="shipped"),
        OrderStatusUpdate(status="completed"),
        OrderStatusUpdate(status="assembling", delivery_status="assembling"),
        OrderStatusUpdate(status="assembling", tracking_number="TRACK-17"),
    ],
)
def test_generic_patch_rejects_later_or_shipment_owned_mutations(monkeypatch, payload):
    db = FakeSession()
    monkeypatch.setattr(order_cancellation, "require_permission", lambda *_args: None)

    with pytest.raises(HTTPException) as exc_info:
        order_cancellation.start_admin_fulfillment_via_generic_patch(
            17,
            payload,
            admin=_admin(),
            db=db,
        )

    assert exc_info.value.status_code == 409
    assert db.commits == 0


def test_generic_fulfillment_start_rejects_unsettled_or_empty_orders(monkeypatch):
    db = FakeSession()
    monkeypatch.setattr(order_cancellation, "require_permission", lambda *_args: None)
    monkeypatch.setattr(order_cancellation, "_load_locked_fulfillment_task", lambda *_args: None)

    for order in (
        _paid_order(status="created", payment_status="pending"),
        _paid_order(items=[]),
    ):
        monkeypatch.setattr(order_cancellation, "_load_locked_order", lambda *_args, value=order: value)
        with pytest.raises(HTTPException) as exc_info:
            order_cancellation.start_admin_fulfillment_via_generic_patch(
                order.id,
                OrderStatusUpdate(status="assembling"),
                admin=_admin(),
                db=db,
            )
        assert exc_info.value.status_code == 409


def test_safe_admin_order_gateway_is_physical_and_unique():
    monolith = (ROOT / "api" / "admin.py").read_text(encoding="utf-8")
    source = (ROOT / "api" / "order_cancellation.py").read_text(encoding="utf-8")

    assert "def admin_update_order(" not in monolith
    assert '@router.patch("/orders/{order_id}"' not in monolith
    assert "_replace_legacy_admin_order_patch" not in source
    assert "admin_router.routes" not in source
    assert "APIRoute" not in source

    matching = [
        route
        for route in order_cancellation.router.routes
        if isinstance(route, APIRoute)
        and route.path == "/admin/orders/{order_id}"
        and "PATCH" in route.methods
    ]
    assert len(matching) == 1
    assert matching[0].endpoint is order_cancellation.start_admin_fulfillment_via_generic_patch

    assert str(app.url_path_for("start_admin_fulfillment_via_generic_patch", order_id=17)) == "/api/admin/orders/17"
    operation = app.openapi()["paths"]["/api/admin/orders/{order_id}"]["patch"]
    assert operation["operationId"].startswith("start_admin_fulfillment_via_generic_patch")
