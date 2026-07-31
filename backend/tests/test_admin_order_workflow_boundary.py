import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from backend.api import order_cancellation
from backend.main import app
from backend.schemas import OrderStatusUpdate


class _Query:
    def __init__(self, exists: bool):
        self.exists = exists

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return (1,) if self.exists else None


class _Db:
    def __init__(self, exists: bool = True):
        self.exists = exists
        self.rollbacks = 0

    def query(self, *_args, **_kwargs):
        return _Query(self.exists)

    def rollback(self):
        self.rollbacks += 1


@pytest.mark.parametrize(
    ("payload", "expected_fields"),
    [
        (OrderStatusUpdate(status="paid"), ["status"]),
        (OrderStatusUpdate(delivery_status="shipped"), ["delivery_status"]),
        (OrderStatusUpdate(tracking_number="TRACK-1"), ["tracking_number"]),
        (
            OrderStatusUpdate(status="cancelled", delivery_status="cancelled"),
            ["delivery_status", "status"],
        ),
    ],
)
def test_endpoint_shadow_rejects_every_managed_order_field(
    monkeypatch,
    payload,
    expected_fields,
):
    permissions = []
    monkeypatch.setattr(
        order_cancellation,
        "require_permission",
        lambda _db, _admin, permission: permissions.append(permission),
    )
    db = _Db()

    with pytest.raises(HTTPException) as exc_info:
        order_cancellation.reject_generic_admin_order_update(
            42,
            payload,
            admin=object(),
            db=db,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["managed_fields"] == expected_fields
    assert exc_info.value.detail["safe_cancellation_endpoint"] == (
        "/api/admin/orders/42/cancel-safe"
    )
    assert permissions == ["orders.write"]
    assert db.rollbacks == 1


def test_endpoint_shadow_rejects_empty_legacy_patch(monkeypatch):
    monkeypatch.setattr(order_cancellation, "require_permission", lambda *_args: None)
    db = _Db()

    with pytest.raises(HTTPException) as exc_info:
        order_cancellation.reject_generic_admin_order_update(
            42,
            OrderStatusUpdate(),
            admin=object(),
            db=db,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["managed_fields"] == []
    assert db.rollbacks == 1


def test_endpoint_shadow_preserves_not_found_semantics(monkeypatch):
    monkeypatch.setattr(order_cancellation, "require_permission", lambda *_args: None)
    db = _Db(exists=False)

    with pytest.raises(HTTPException) as exc_info:
        order_cancellation.reject_generic_admin_order_update(
            999,
            OrderStatusUpdate(status="cancelled"),
            admin=object(),
            db=db,
        )

    assert exc_info.value.status_code == 404
    assert db.rollbacks == 1


def test_safe_endpoint_is_registered_before_legacy_admin_patch():
    matching = [
        route
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path == "/api/admin/orders/{order_id}"
        and "PATCH" in route.methods
    ]

    assert len(matching) == 2
    assert matching[0].endpoint is order_cancellation.reject_generic_admin_order_update
    assert matching[1].endpoint.__module__ == "backend.api.admin"


def test_middleware_and_endpoint_boundary_are_both_installed():
    middleware_names = {item.cls.__name__ for item in app.user_middleware}

    assert "AdminOrderStateGuardMiddleware" in middleware_names
    assert any(
        isinstance(route, APIRoute)
        and route.endpoint is order_cancellation.reject_generic_admin_order_update
        for route in app.routes
    )
