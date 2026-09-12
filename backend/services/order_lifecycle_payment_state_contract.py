from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..order_statuses import SETTLED_ORDER_PAYMENT_STATUSES


_STATUS_RANK = {"PASS": 0, "PENDING": 1, "REVIEW": 2, "BLOCKED": 3}
_SETTLED_PAYMENT_RECORD_STATUSES = {
    "paid",
    "succeeded",
    "partially_refunded",
    "refunded",
}
_REFUND_REVIEW_PAYMENT_STATUSES = {"refund_review_required"}
_FULL_REFUND_PAYMENT_STATUS = "refunded"


def _status(value: Any) -> str:
    return str(value or "").strip().lower()


def _items(trace: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = trace.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _stage(stages: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    return next(
        (item for item in stages if isinstance(item, dict) and item.get("key") == key),
        None,
    )


def _strictest_stage_status(stages: list[dict[str, Any]]) -> str:
    status = "PASS"
    for item in stages:
        candidate = str(item.get("status") or "REVIEW")
        if candidate not in _STATUS_RANK:
            candidate = "REVIEW"
        if _STATUS_RANK[candidate] > _STATUS_RANK[status]:
            status = candidate
    return status


def _movement_kinds(trace: dict[str, Any]) -> set[str]:
    return {_status(item.get("kind")) for item in _items(trace, "inventory")}


def enforce_settled_order_payment_state_contract(
    reconciliation: dict[str, Any],
    trace: dict[str, Any],
) -> dict[str, Any]:
    """Align lifecycle semantics with canonical order-level settlement states.

    Order.payment_status and Payment.status are intentionally separate domains.
    The canonical order settlement set includes refund-processing states that
    still prove the original payment was settled; those states must never be
    described to operators as pre-payment inventory/fulfillment states.
    """

    result = deepcopy(reconciliation)
    stages = result.get("stages") if isinstance(result.get("stages"), list) else []
    order = trace.get("order") if isinstance(trace.get("order"), dict) else {}
    order_status = _status(order.get("status"))
    payment_status = _status(order.get("payment_status"))
    delivery_status = _status(order.get("delivery_status"))
    if payment_status not in SETTLED_ORDER_PAYMENT_STATUSES:
        return result

    payments = _items(trace, "payments")
    has_settled_payment_record = any(
        _status(item.get("status")) in _SETTLED_PAYMENT_RECORD_STATUSES
        for item in payments
    )
    payment = _stage(stages, "payment")
    inventory = _stage(stages, "inventory")
    fulfillment = _stage(stages, "fulfillment")

    if payment is not None and payment.get("status") != "BLOCKED":
        if not has_settled_payment_record:
            payment.update(
                {
                    "status": "BLOCKED",
                    "reason": "order_paid_without_settled_payment_record",
                    "next_action": "inspect_payment_review",
                    "evidence": [f"order.payment_status={payment_status}"],
                }
            )
        elif payment_status in _REFUND_REVIEW_PAYMENT_STATUSES:
            payment.update(
                {
                    "status": "REVIEW",
                    "reason": "payment_review_required",
                    "next_action": "inspect_payment_review",
                    "evidence": [f"order.payment_status={payment_status}"],
                }
            )
        elif payment.get("status") == "PENDING":
            payment.update(
                {
                    "status": "PASS",
                    "reason": "payment_settled",
                    "next_action": "none",
                    "evidence": [f"order.payment_status={payment_status}"],
                }
            )

    kinds = _movement_kinds(trace)
    if inventory is not None and inventory.get("status") not in {"BLOCKED", "REVIEW"}:
        if payment_status == _FULL_REFUND_PAYMENT_STATUS or order_status == "refunded":
            # Full-refund inventory semantics remain owned by the base evaluator:
            # commit without return is REVIEW, returned inventory is PASS.
            pass
        elif "commit" in kinds:
            inventory.update(
                {
                    "status": "PASS",
                    "reason": "inventory_commit_recorded",
                    "next_action": "none",
                    "evidence": ["inventory.kind=commit"],
                }
            )
        elif "reserve" in kinds:
            inventory.update(
                {
                    "status": "PENDING",
                    "reason": "inventory_reserved_not_committed_yet",
                    "next_action": "wait_for_fulfillment",
                    "evidence": ["inventory.kind=reserve"],
                }
            )
        elif order_status in {"shipped", "completed", "refund_requested", "partially_refunded"} or delivery_status in {
            "shipped",
            "in_transit",
            "out_for_delivery",
            "delivered",
        }:
            inventory.update(
                {
                    "status": "REVIEW",
                    "reason": "paid_order_inventory_not_recorded",
                    "next_action": "inspect_inventory_ledger",
                    "evidence": [f"order.payment_status={payment_status}"],
                }
            )
        else:
            inventory.update(
                {
                    "status": "PENDING",
                    "reason": "paid_order_inventory_not_recorded",
                    "next_action": "wait_for_fulfillment",
                    "evidence": [f"order.payment_status={payment_status}"],
                }
            )

    if fulfillment is not None and fulfillment.get("status") == "PENDING":
        if fulfillment.get("reason") == "fulfillment_not_expected_before_payment":
            fulfillment.update(
                {
                    "reason": "paid_order_fulfillment_not_started",
                    "next_action": "wait_for_fulfillment",
                    "evidence": [f"order.payment_status={payment_status}"],
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
