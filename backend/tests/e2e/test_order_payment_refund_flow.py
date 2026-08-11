"""Verify a completed real-provider pilot lifecycle without creating side effects.

This is intentionally separate from the order/payment creation runner. It is a
read-only verifier for an operator-provided order that has already completed the
real YooKassa -> fulfillment -> refund -> notification lifecycle.

RUN_REAL_LIFECYCLE_E2E=1 \
API_BASE=https://api.example.test \
CUSTOMER_TOKEN=... ADMIN_TOKEN=... \
E2E_ORDER_ID=123 E2E_VARIANT_ID=456 E2E_EXPECTED_STOCK_QTY=2 \
pytest -q backend/tests/e2e/test_order_payment_refund_flow.py
"""

import os

import pytest
import requests

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REAL_LIFECYCLE_E2E") != "1",
    reason="set RUN_REAL_LIFECYCLE_E2E=1 for terminal real-provider verification",
)

API = os.getenv("API_BASE", "http://localhost:8000").rstrip("/")
CUSTOMER = os.getenv("CUSTOMER_TOKEN", "")
ADMIN = os.getenv("ADMIN_TOKEN", "")


def headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _required_int(name: str) -> int:
    raw = str(os.getenv(name, "")).strip()
    assert raw.isdigit() and int(raw) > 0, f"{name} must be a positive integer"
    return int(raw)


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
    order_id = _required_int("E2E_ORDER_ID")
    variant_id = _required_int("E2E_VARIANT_ID")
    expected_stock = _required_int("E2E_EXPECTED_STOCK_QTY")

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
    assert completed_returns, "Expected one approved provider-backed return for the pilot order"
    return_request = completed_returns[0]
    return_id = int(return_request["id"])

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
        f"{API}/api/admin/notifications",
        headers=headers(ADMIN),
        timeout=20,
    )
    assert notifications_response.status_code == 200, notifications_response.text
    refund_notifications = [
        row
        for row in notifications_response.json()
        if f"заказу #{order_id}" in str(row.get("message", "")).lower()
        and "возвращена" in str(row.get("message", "")).lower()
    ]
    assert refund_notifications, "Expected refund notification for the pilot order"
    assert all(row.get("status") == "sent" for row in refund_notifications)
    assert all(row.get("sent_at") for row in refund_notifications)

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
