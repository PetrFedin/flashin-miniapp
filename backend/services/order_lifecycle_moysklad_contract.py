from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..order_statuses import SETTLED_ORDER_PAYMENT_STATUSES


_STATUS_RANK = {"PASS": 0, "PENDING": 1, "REVIEW": 2, "BLOCKED": 3}
_CUSTOMER_ORDER = "moysklad.customer_order.create"
_DEMAND = "moysklad.demand.create"
_SALES_RETURN = "moysklad.sales_return.create"


def _status(value: Any) -> str:
    return str(value or "").strip().lower()


def _required_commands(trace: dict[str, Any]) -> set[str]:
    order = trace.get("order") if isinstance(trace.get("order"), dict) else {}
    order_status = _status(order.get("status"))
    payment_status = _status(order.get("payment_status"))
    delivery_status = _status(order.get("delivery_status"))
    required: set[str] = set()

    if payment_status in SETTLED_ORDER_PAYMENT_STATUSES or order_status in {
        "paid", "assembling", "ready", "shipped", "completed", "refund_requested", "partially_refunded", "refunded"
    }:
        required.add(_CUSTOMER_ORDER)
    if order_status in {"shipped", "completed", "refunded"} or delivery_status in {
        "shipped", "in_transit", "out_for_delivery", "delivered"
    }:
        required.add(_DEMAND)
    if payment_status == "refunded" or order_status == "refunded":
        required.add(_SALES_RETURN)
    return required


def _command_types(trace: dict[str, Any]) -> set[str]:
    commands = trace.get("provider_commands")
    if not isinstance(commands, list):
        return set()
    result: set[str] = set()
    for item in commands:
        if not isinstance(item, dict):
            continue
        command_type = _status(item.get("command_type"))
        provider = _status(item.get("provider"))
        if provider == "moysklad" or command_type.startswith("moysklad."):
            result.add(command_type)
    return result


def _strictest_stage_status(stages: list[dict[str, Any]]) -> str:
    status = "PASS"
    for item in stages:
        candidate = str(item.get("status") or "REVIEW")
        if candidate not in _STATUS_RANK:
            candidate = "REVIEW"
        if _STATUS_RANK[candidate] > _STATUS_RANK[status]:
            status = candidate
    return status


def enforce_moysklad_lifecycle_contract(
    reconciliation: dict[str, Any],
    trace: dict[str, Any],
) -> dict[str, Any]:
    """Ensure lifecycle states contain every expected MoySklad command."""

    result = deepcopy(reconciliation)
    stages = result.get("stages") if isinstance(result.get("stages"), list) else []
    moysklad = next(
        (item for item in stages if isinstance(item, dict) and item.get("key") == "moysklad"),
        None,
    )
    if moysklad is None or moysklad.get("status") == "BLOCKED":
        return result

    required = _required_commands(trace)
    missing = sorted(required - _command_types(trace))
    if not missing:
        return result

    order = trace.get("order") if isinstance(trace.get("order"), dict) else {}
    order_status = _status(order.get("status"))
    payment_status = _status(order.get("payment_status"))
    delivery_status = _status(order.get("delivery_status"))
    should_review = bool(
        order_status in {"shipped", "completed", "refunded"}
        or payment_status == "refunded"
        or delivery_status in {"shipped", "in_transit", "out_for_delivery", "delivered"}
    )
    moysklad.update(
        {
            "status": "REVIEW" if should_review else "PENDING",
            "reason": "moysklad_required_command_missing",
            "next_action": "inspect_moysklad_command_queue" if should_review else "wait_for_provider_command",
            "evidence": [f"moysklad.missing={','.join(missing)}"],
        }
    )

    overall = _strictest_stage_status(stages)
    supplied = str(result.get("overall_status") or "REVIEW")
    if supplied not in _STATUS_RANK:
        supplied = "REVIEW"
    if _STATUS_RANK[supplied] > _STATUS_RANK[overall]:
        overall = supplied
    result["overall_status"] = overall
    result["requires_operator_action"] = bool(
        result.get("requires_operator_action") or overall in {"REVIEW", "BLOCKED"}
    )
    return result