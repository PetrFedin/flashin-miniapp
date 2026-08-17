from __future__ import annotations

from typing import Any


PASS = "PASS"
PENDING = "PENDING"
REVIEW = "REVIEW"
BLOCKED = "BLOCKED"

_STATUS_RANK = {PASS: 0, PENDING: 1, REVIEW: 2, BLOCKED: 3}

_PAID_LIKE = {"paid", "succeeded", "partially_refunded", "refunded"}
_CANCELLED = {"canceled", "cancelled"}
_TERMINAL_ORDER = _CANCELLED | {"completed", "refunded"}
_PROVIDER_SUCCESS = {"sent", "succeeded", "processed", "completed"}
_PROVIDER_PENDING = {"pending", "processing", "in_progress", "new"}
_PROVIDER_FAILED = {"failed", "dead", "review_required"}
_RETURN_SUCCESS = {"approved", "approved_partial"}
_RETURN_PENDING = {"requested", "processing", "refund_pending"}
_RETURN_REVIEW = {"refund_retry_required", "refund_review_required"}


def _status(value: Any) -> str:
    return str(value or "").strip().lower()


def _items(trace: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = trace.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _stage(
    key: str,
    status: str,
    reason: str,
    next_action: str,
    *evidence: str,
) -> dict[str, Any]:
    if status not in _STATUS_RANK:
        raise ValueError(f"Unsupported lifecycle status: {status}")
    return {
        "key": key,
        "status": status,
        "reason": reason,
        "next_action": next_action,
        "evidence": [str(item) for item in evidence if str(item).strip()],
    }


def _order_state(trace: dict[str, Any]) -> tuple[str, str, str]:
    order = trace.get("order") if isinstance(trace.get("order"), dict) else {}
    return (
        _status(order.get("status")),
        _status(order.get("payment_status")),
        _status(order.get("delivery_status")),
    )


def _has_overdue_sla(trace: dict[str, Any]) -> bool:
    attention = trace.get("attention") if isinstance(trace.get("attention"), dict) else {}
    return int(attention.get("overdue_sla") or 0) > 0


def _payment_stage(trace: dict[str, Any]) -> dict[str, Any]:
    order_status, order_payment_status, _delivery_status = _order_state(trace)
    payments = _items(trace, "payments")
    payment_statuses = [_status(item.get("status")) for item in payments]
    review_required = order_payment_status in {
        "paid_review_required",
        "refund_review_required",
    } or any(status == "review_required" for status in payment_statuses)

    if review_required:
        return _stage(
            "payment",
            REVIEW,
            "payment_review_required",
            "inspect_payment_review",
            f"order.payment_status={order_payment_status or 'missing'}",
        )

    if any(status in {"failed", "error", "dead"} for status in payment_statuses):
        if order_status in _CANCELLED and not any(status in _PAID_LIKE for status in payment_statuses):
            return _stage(
                "payment",
                PASS,
                "cancelled_without_captured_payment",
                "none",
                f"order.status={order_status}",
            )
        return _stage(
            "payment",
            BLOCKED,
            "payment_terminal_failure",
            "inspect_payment_review",
            "payments.status=failed",
        )

    paid_payments = [item for item in payments if _status(item.get("status")) in _PAID_LIKE]
    order_paid_like = order_payment_status in _PAID_LIKE
    if paid_payments or order_paid_like:
        if not paid_payments:
            return _stage(
                "payment",
                BLOCKED,
                "order_paid_without_settled_payment_record",
                "inspect_payment_review",
                f"order.payment_status={order_payment_status}",
            )
        order = trace.get("order") if isinstance(trace.get("order"), dict) else {}
        order_amount = float(order.get("total_amount") or 0)
        payment_amount = float(paid_payments[-1].get("amount") or 0)
        if order_amount > 0 and abs(order_amount - payment_amount) > 0.01:
            return _stage(
                "payment",
                BLOCKED,
                "settled_payment_amount_mismatch",
                "inspect_payment_review",
                f"order.total_amount={order_amount:.2f}",
                f"payment.amount={payment_amount:.2f}",
            )
        return _stage(
            "payment",
            PASS,
            "payment_settled",
            "none",
            f"payment.status={_status(paid_payments[-1].get('status'))}",
        )

    if order_status in _CANCELLED:
        return _stage(
            "payment",
            PASS,
            "cancelled_without_captured_payment",
            "none",
            f"order.status={order_status}",
        )

    return _stage(
        "payment",
        PENDING,
        "payment_not_settled_yet",
        "wait_for_payment_callback",
        f"order.payment_status={order_payment_status or 'missing'}",
    )


def _movement_invalid(item: dict[str, Any]) -> bool:
    try:
        quantity = int(item.get("quantity") or 0)
        stock_before = int(item.get("stock_before") or 0)
        stock_after = int(item.get("stock_after") or 0)
        reserved_before = int(item.get("reserved_before") or 0)
        reserved_after = int(item.get("reserved_after") or 0)
    except (TypeError, ValueError):
        return True
    return bool(
        quantity <= 0
        or stock_before < 0
        or stock_after < 0
        or reserved_before < 0
        or reserved_after < 0
        or reserved_before > stock_before
        or reserved_after > stock_after
    )


def _inventory_stage(trace: dict[str, Any]) -> dict[str, Any]:
    order_status, payment_status, _delivery_status = _order_state(trace)
    movements = _items(trace, "inventory")
    kinds = [_status(item.get("kind")) for item in movements]
    invalid_count = sum(1 for item in movements if _movement_invalid(item))
    if invalid_count:
        return _stage(
            "inventory",
            BLOCKED,
            "inventory_ledger_invalid",
            "inspect_inventory_ledger",
            f"inventory.invalid_rows={invalid_count}",
        )

    if order_status in _CANCELLED:
        if "commit" in kinds and not ({"release", "return"} & set(kinds)):
            return _stage(
                "inventory",
                REVIEW,
                "cancelled_order_has_unreversed_commit",
                "inspect_inventory_ledger",
                "inventory.kind=commit",
            )
        return _stage(
            "inventory",
            PASS,
            "cancelled_order_inventory_reconciled",
            "none",
            f"inventory.movements={len(movements)}",
        )

    if payment_status == "refunded" or order_status == "refunded":
        if "commit" in kinds and "return" not in kinds:
            return _stage(
                "inventory",
                REVIEW,
                "refunded_order_missing_inventory_return",
                "inspect_inventory_ledger",
                "inventory.kind=return_missing",
            )
        return _stage(
            "inventory",
            PASS,
            "refund_inventory_reconciled",
            "none",
            f"inventory.movements={len(movements)}",
        )

    if payment_status in _PAID_LIKE or order_status in {"paid", "confirmed", "processing", "shipped", "completed"}:
        if "commit" in kinds:
            return _stage(
                "inventory",
                PASS,
                "inventory_commit_recorded",
                "none",
                "inventory.kind=commit",
            )
        if order_status in {"shipped", "completed"}:
            return _stage(
                "inventory",
                BLOCKED,
                "fulfilled_order_missing_inventory_commit",
                "inspect_inventory_ledger",
                f"order.status={order_status}",
            )
        if "reserve" in kinds:
            return _stage(
                "inventory",
                PENDING,
                "inventory_reserved_not_committed_yet",
                "wait_for_fulfillment",
                "inventory.kind=reserve",
            )
        return _stage(
            "inventory",
            REVIEW if _has_overdue_sla(trace) else PENDING,
            "paid_order_inventory_not_recorded",
            "inspect_inventory_ledger" if _has_overdue_sla(trace) else "wait_for_fulfillment",
            f"inventory.movements={len(movements)}",
        )

    return _stage(
        "inventory",
        PENDING,
        "inventory_not_expected_before_payment",
        "wait_for_payment_callback",
        f"inventory.movements={len(movements)}",
    )


def _moysklad_stage(trace: dict[str, Any]) -> dict[str, Any]:
    order_status, payment_status, _delivery_status = _order_state(trace)
    commands = [
        item
        for item in _items(trace, "provider_commands")
        if _status(item.get("provider")) == "moysklad"
        or _status(item.get("command_type")).startswith("moysklad.")
    ]
    statuses = [_status(item.get("status")) for item in commands]

    if any(status in _PROVIDER_FAILED for status in statuses):
        return _stage(
            "moysklad",
            BLOCKED,
            "moysklad_command_failed",
            "inspect_moysklad_command_queue",
            "provider=moysklad",
            "command.status=failed_or_review",
        )
    if any(status not in _PROVIDER_SUCCESS | _PROVIDER_PENDING for status in statuses):
        return _stage(
            "moysklad",
            REVIEW,
            "moysklad_command_status_unknown",
            "inspect_moysklad_command_queue",
            "provider=moysklad",
        )
    if any(status in _PROVIDER_PENDING for status in statuses):
        status = REVIEW if order_status in _TERMINAL_ORDER and _has_overdue_sla(trace) else PENDING
        return _stage(
            "moysklad",
            status,
            "moysklad_command_in_progress" if status == PENDING else "terminal_order_moysklad_not_terminal",
            "wait_for_provider_command" if status == PENDING else "inspect_moysklad_command_queue",
            "provider=moysklad",
            "command.status=pending_or_processing",
        )
    if commands and all(status in _PROVIDER_SUCCESS for status in statuses):
        return _stage(
            "moysklad",
            PASS,
            "moysklad_commands_terminal_success",
            "none",
            f"moysklad.commands={len(commands)}",
        )

    if order_status in _CANCELLED and payment_status not in _PAID_LIKE:
        return _stage(
            "moysklad",
            PASS,
            "moysklad_not_required_for_unpaid_cancellation",
            "none",
            "moysklad.commands=0",
        )
    if order_status in {"completed", "refunded"} or payment_status == "refunded":
        return _stage(
            "moysklad",
            REVIEW,
            "terminal_order_missing_moysklad_command",
            "inspect_moysklad_command_queue",
            "moysklad.commands=0",
        )
    return _stage(
        "moysklad",
        PENDING,
        "moysklad_command_not_terminal_yet",
        "wait_for_provider_command",
        f"moysklad.commands={len(commands)}",
    )


def _fulfillment_stage(trace: dict[str, Any]) -> dict[str, Any]:
    order_status, payment_status, delivery_status = _order_state(trace)
    tasks = _items(trace, "fulfillment")
    task_statuses = [_status(item.get("status")) for item in tasks]

    if order_status in _CANCELLED:
        return _stage(
            "fulfillment",
            PASS,
            "fulfillment_not_required_for_cancelled_order",
            "none",
            f"order.status={order_status}",
        )
    if payment_status in {"paid_review_required", "refund_review_required"}:
        return _stage(
            "fulfillment",
            REVIEW,
            "fulfillment_held_for_payment_review",
            "inspect_payment_review",
            f"order.payment_status={payment_status}",
        )
    if order_status == "completed" or delivery_status == "delivered":
        if not tasks:
            return _stage(
                "fulfillment",
                BLOCKED,
                "completed_order_missing_fulfillment_task",
                "inspect_fulfillment",
                "fulfillment.tasks=0",
            )
        return _stage(
            "fulfillment",
            PASS,
            "fulfillment_completed",
            "none",
            f"order.delivery_status={delivery_status or 'completed'}",
        )
    if order_status == "shipped" or delivery_status in {"shipped", "in_transit", "out_for_delivery"}:
        return _stage(
            "fulfillment",
            PENDING,
            "shipment_in_progress",
            "wait_for_delivery",
            f"order.delivery_status={delivery_status or order_status}",
        )
    if tasks:
        if any(status not in {"pick_pending", "picking", "packed"} for status in task_statuses):
            return _stage(
                "fulfillment",
                REVIEW,
                "fulfillment_task_status_unknown",
                "inspect_fulfillment",
                f"fulfillment.tasks={len(tasks)}",
            )
        if _has_overdue_sla(trace):
            return _stage(
                "fulfillment",
                REVIEW,
                "fulfillment_sla_overdue",
                "inspect_fulfillment",
                f"fulfillment.tasks={len(tasks)}",
            )
        return _stage(
            "fulfillment",
            PENDING,
            "fulfillment_in_progress",
            "wait_for_fulfillment",
            f"fulfillment.tasks={len(tasks)}",
        )
    if payment_status in _PAID_LIKE or order_status in {"paid", "confirmed", "processing"}:
        return _stage(
            "fulfillment",
            REVIEW if _has_overdue_sla(trace) else PENDING,
            "paid_order_fulfillment_not_started",
            "inspect_fulfillment" if _has_overdue_sla(trace) else "wait_for_fulfillment",
            "fulfillment.tasks=0",
        )
    return _stage(
        "fulfillment",
        PENDING,
        "fulfillment_not_expected_before_payment",
        "wait_for_payment_callback",
        "fulfillment.tasks=0",
    )


def _refund_stage(trace: dict[str, Any]) -> dict[str, Any]:
    returns = _items(trace, "returns")
    if not returns:
        return _stage(
            "refunds",
            PASS,
            "no_refund_requested",
            "none",
            "returns.count=0",
        )
    statuses = [_status(item.get("status")) for item in returns]
    if any(status in _RETURN_REVIEW for status in statuses):
        return _stage(
            "refunds",
            REVIEW,
            "refund_reconciliation_required",
            "inspect_refund_reconciliation",
            "return.status=review_or_retry_required",
        )
    if any(status in {"failed", "dead", "error"} for status in statuses):
        return _stage(
            "refunds",
            BLOCKED,
            "refund_terminal_failure",
            "inspect_refund_reconciliation",
            "return.status=failed",
        )
    if any(status in _RETURN_PENDING for status in statuses):
        return _stage(
            "refunds",
            PENDING,
            "refund_in_progress",
            "wait_for_refund_settlement",
            "return.status=pending_or_processing",
        )
    if all(status in _RETURN_SUCCESS for status in statuses):
        return _stage(
            "refunds",
            PASS,
            "refunds_settled",
            "none",
            f"returns.count={len(returns)}",
        )
    return _stage(
        "refunds",
        REVIEW,
        "refund_status_unknown",
        "inspect_refund_reconciliation",
        f"returns.count={len(returns)}",
    )


def _notification_stage(trace: dict[str, Any]) -> dict[str, Any]:
    order_status, payment_status, _delivery_status = _order_state(trace)
    notifications = _items(trace, "notifications")
    statuses = [_status(item.get("status")) for item in notifications]
    if any(status == "failed" for status in statuses):
        return _stage(
            "notifications",
            REVIEW,
            "notification_delivery_failed",
            "inspect_notification_delivery",
            "notification.status=failed",
        )
    if any(status in {"pending", "processing", "retry"} for status in statuses):
        return _stage(
            "notifications",
            PENDING,
            "notification_delivery_in_progress",
            "wait_for_notification_delivery",
            "notification.status=pending_or_processing",
        )
    if notifications and all(status in {"sent", "delivered"} for status in statuses):
        return _stage(
            "notifications",
            PASS,
            "notifications_delivered",
            "none",
            f"notifications.count={len(notifications)}",
        )
    if order_status in _TERMINAL_ORDER or payment_status in _PAID_LIKE:
        return _stage(
            "notifications",
            REVIEW,
            "settled_order_has_no_notification_evidence",
            "inspect_notification_delivery",
            "notifications.count=0",
        )
    return _stage(
        "notifications",
        PENDING,
        "notification_not_expected_yet",
        "wait_for_payment_callback",
        "notifications.count=0",
    )


def evaluate_order_lifecycle(trace: dict[str, Any]) -> dict[str, Any]:
    """Evaluate a sanitized order trace into stable, non-mutating operator verdicts.

    The evaluator consumes only the already-sanitized operations-trace contract.
    It never receives raw provider payloads, credentials, Telegram identifiers,
    notification bodies, idempotency keys or free-form provider errors.
    """

    stages = [
        _payment_stage(trace),
        _inventory_stage(trace),
        _moysklad_stage(trace),
        _fulfillment_stage(trace),
        _refund_stage(trace),
        _notification_stage(trace),
    ]
    overall_status = max(stages, key=lambda item: _STATUS_RANK[item["status"]])["status"]
    requires_operator_action = overall_status in {REVIEW, BLOCKED}
    return {
        "schema_version": 1,
        "overall_status": overall_status,
        "requires_operator_action": requires_operator_action,
        "stages": stages,
    }
