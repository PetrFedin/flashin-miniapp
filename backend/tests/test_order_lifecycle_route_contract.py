from types import SimpleNamespace

from fastapi import Response

from backend.api import ops


def test_order_trace_route_composes_lifecycle_contracts_read_only(monkeypatch):
    permissions = []
    calls = []
    raw_trace = {
        "schema_version": 2,
        "order": {"id": 42, "status": "refund_requested", "payment_status": "refund_pending", "delivery_status": "pending"},
        "provider_commands": [],
        "attention": {"business_events_unresolved": 1, "business_events_failed": 0},
    }
    evaluated = {
        "schema_version": 1,
        "overall_status": "PENDING",
        "requires_operator_action": False,
        "stages": [{"key": "moysklad", "status": "PENDING"}],
    }
    settled = {**evaluated, "settled": True}
    contracted = {**settled, "contracted": True}
    signaled = {**contracted, "operational_signals": [{"key": "business_events", "status": "PENDING"}]}

    monkeypatch.setattr(ops, "require_permission", lambda db, admin, permission: permissions.append(permission))
    monkeypatch.setattr(ops, "build_order_operations_trace", lambda db, order_id: dict(raw_trace))
    monkeypatch.setattr(
        ops,
        "evaluate_order_lifecycle",
        lambda trace: calls.append(("evaluate", trace["schema_version"])) or dict(evaluated),
    )
    monkeypatch.setattr(
        ops,
        "enforce_settled_order_payment_state_contract",
        lambda reconciliation, trace: calls.append(("settlement", trace["order"]["payment_status"])) or dict(settled),
    )
    monkeypatch.setattr(
        ops,
        "enforce_moysklad_lifecycle_contract",
        lambda reconciliation, trace: calls.append(("moysklad", trace["order"]["id"])) or dict(contracted),
    )
    monkeypatch.setattr(
        ops,
        "apply_operational_signals",
        lambda reconciliation, trace: calls.append(("signals", trace["attention"]["business_events_unresolved"])) or dict(signaled),
    )

    response = Response()
    result = ops.order_operations_trace(
        42,
        request=SimpleNamespace(state=SimpleNamespace(request_id="request-lifecycle-42")),
        response=response,
        admin=SimpleNamespace(id=7),
        db=object(),
    )

    assert permissions == ["orders.read"]
    assert calls == [
        ("evaluate", 3),
        ("settlement", "refund_pending"),
        ("moysklad", 42),
        ("signals", 1),
    ]
    assert result["schema_version"] == 3
    assert result["request_id"] == "request-lifecycle-42"
    assert result["reconciliation"] == signaled
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"