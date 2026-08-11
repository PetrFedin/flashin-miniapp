"""Verify a completed real-provider pilot lifecycle without creating side effects.

The side-effectful real-order runner writes a sanitized context artifact with the
exact order, controlled SKU and pre-order stock. This verifier consumes that
artifact so its terminal assertions cannot be pointed at a different order/SKU.

RUN_REAL_LIFECYCLE_E2E=1 \
API_BASE=https://api.example.test CUSTOMER_TOKEN=... ADMIN_TOKEN=... \
pytest -q backend/tests/e2e/test_order_payment_refund_flow.py
"""

import json
import os
from pathlib import Path

import pytest
import requests

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REAL_LIFECYCLE_E2E") != "1",
    reason="set RUN_REAL_LIFECYCLE_E2E=1 for terminal real-provider verification",
)

API = os.getenv("API_BASE", "http://localhost:8000").rstrip("/")
CUSTOMER = os.getenv("CUSTOMER_TOKEN", "")
ADMIN = os.getenv("ADMIN_TOKEN", "")
CONTEXT_FILE = Path(
    os.getenv(
        "E2E_CONTEXT_FILE",
        "docs/pilot/evidence/real_order_e2e_context.json",
    )
)


def headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _positive_context_int(context: dict[str, object], key: str) -> int:
    raw = context.get(key)
    assert not isinstance(raw, bool), f"context {key} must be a positive integer"
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise AssertionError(f"context {key} must be a positive integer") from None
    assert value > 0, f"context {key} must be a positive integer"
    return value


def _load_context() -> dict[str, object]:
    assert CONTEXT_FILE.is_file(), f"Real E2E context file is missing: {CONTEXT_FILE}"
    try:
        context = json.loads(CONTEXT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssertionError(f"Real E2E context file is invalid: {exc}") from exc
    assert isinstance(context, dict), "Real E2E context must be a JSON object"
    assert context.get("schema_version") == 1, context
    assert context.get("kind") == "flashin_real_order_e2e_context", context
    assert str(context.get("api_base") or "").rstrip("/") == API, (
        "Real E2E context belongs to a different API_BASE"
    )
    assert context.get("provider") == "yookassa", context
    assert str(context.get("provider_payment_id") or "").strip(), context
    assert str(context.get("subject_id") or "").strip(), context
    return context


def _assert_sent_command(
    commands: list[dict[str, object]],
    *,
    command_type: str,
    aggregate_type: str,
    aggregate_id: int,
) -> dict[str, object]:
    matching = [
        row
        for row in commands
        if row.get("command_type") == command_type
        and row.get("aggregate_type") == aggregate_type
        and row.get("aggregate_id") == str(aggregate_id)
    ]
    assert len(matching) == 1, (
        f"Expected exactly one {command_type} command for "
        f"{aggregate_type} {aggregate_id}, found {len(matching)}"
    )
    command = matching[0]
    assert command.get("status") == "sent", command
    assert str(command.get("external_id") or "").strip(), command
    return command


def test_completed_real_refund_lifecycle_is_consistent():
    assert CUSTOMER, "CUSTOMER_TOKEN required"
    assert ADMIN, "ADMIN_TOKEN required"
    context = _load_context()
    order_id = _positive_context_int(context, "order_id")
    variant_id = _positive_context_int(context, "variant_id")
    expected_stock = _positive_context_int(context, "baseline_stock_qty")
    assert int(context.get("quantity") or 0) == 1, context
    assert int(context.get("baseline_reserved_qty") or -1) == 0, context
    assert context.get("subject_id") == f"order:{order_id}", context

    order_response = requests.get(
        f"{API}/api/orders/{order_id}",
        headers=headers(CUSTOMER),
        timeout=20,
    )
    assert order_response.status_code == 200, order_response.text
    order = order_response.json()
    assert order["id"] == order_id
    assert order["status"] == "refunded"
    assert order["payment_status"] == "refunded"
    assert order["delivery_status"] == "delivered"
    assert len(order.get("items", [])) == 1, order
    controlled_item = order["items"][0]
    assert controlled_item.get("variant_id") == variant_id, controlled_item
    assert controlled_item.get("quantity") == 1, controlled_item

    returns_response = requests.get(
        f"{API}/api/admin/returns",
        headers=headers(ADMIN),
        timeout=20,
    )
    assert returns_response.status_code == 200, returns_response.text
    matching_returns = [row for row in returns_response.json() if row.get("order_id") == order_id]
    assert matching_returns, "Expected a return request for the pilot order"
    completed_returns = [
        row
        for row in matching_returns
        if row.get("status") == "approved" and row.get("provider_refund_id")
    ]
    assert len(completed_returns) == 1, (
        f"Expected exactly one approved provider-backed full refund for order {order_id}, "
        f"found {len(completed_returns)}"
    )
    return_request = completed_returns[0]
    return_id = int(return_request["id"])
    assert float(return_request.get("refundable_balance", -1)) == 0.0, return_request
    assert abs(float(return_request.get("refunded_total", 0)) - float(order["total_amount"])) < 0.01

    tasks_response = requests.get(
        f"{API}/api/fulfillment/tasks",
        headers=headers(ADMIN),
        timeout=20,
    )
    assert tasks_response.status_code == 200, tasks_response.text
    matching_tasks = [row for row in tasks_response.json() if row.get("order_id") == order_id]
    assert matching_tasks, "Expected fulfillment task for the pilot order"
    assert matching_tasks[0].get("status") == "ready"

    products_response = requests.get(
        f"{API}/api/admin/products",
        headers=headers(ADMIN),
        timeout=20,
    )
    assert products_response.status_code == 200, products_response.text
    variants = [
        variant
        for product in products_response.json()
        for variant in product.get("variants", [])
        if variant.get("id") == variant_id
    ]
    assert variants, f"Variant {variant_id} not found"
    assert variants[0].get("stock_qty") == expected_stock
    assert variants[0].get("reserved_qty") == 0

    outbound_response = requests.get(
        f"{API}/api/moysklad/orders/{order_id}/outbound-evidence",
        headers=headers(ADMIN),
        timeout=20,
    )
    assert outbound_response.status_code == 200, outbound_response.text
    outbound = outbound_response.json()
    assert outbound.get("order_id") == order_id
    assert return_id in outbound.get("return_ids", [])
    commands = outbound.get("commands", [])
    assert isinstance(commands, list)
    _assert_sent_command(
        commands,
        command_type="moysklad.customer_order.create",
        aggregate_type="order",
        aggregate_id=order_id,
    )
    _assert_sent_command(
        commands,
        command_type="moysklad.demand.create",
        aggregate_type="order",
        aggregate_id=order_id,
    )
    _assert_sent_command(
        commands,
        command_type="moysklad.sales_return.create",
        aggregate_type="return",
        aggregate_id=return_id,
    )

    notifications_response = requests.get(
        f"{API}/api/admin/notification-delivery?status=sent&limit=200",
        headers=headers(ADMIN),
        timeout=20,
    )
    assert notifications_response.status_code == 200, notifications_response.text
    refund_event_key = f"order:{order_id}:refund:{return_id}:succeeded"
    refund_notifications = [
        row
        for row in notifications_response.json()
        if row.get("event_key") == refund_event_key
    ]
    assert len(refund_notifications) == 1, (
        f"Expected exactly one delivered notification for event {refund_event_key}, "
        f"found {len(refund_notifications)}"
    )
    refund_notification = refund_notifications[0]
    assert refund_notification.get("status") == "sent", refund_notification
    assert refund_notification.get("sent_at"), refund_notification

    diagnostics_response = requests.get(
        f"{API}/api/diagnostics",
        headers=headers(ADMIN),
        timeout=20,
    )
    assert diagnostics_response.status_code == 200, diagnostics_response.text
    diagnostics = diagnostics_response.json().get("checks", {})
    assert diagnostics.get("database", {}).get("ok") is True
    assert diagnostics.get("payments", {}).get("ok") is True
    assert diagnostics.get("moysklad", {}).get("configured") is True
    assert diagnostics.get("notification_delivery", {}).get("ok") is True
