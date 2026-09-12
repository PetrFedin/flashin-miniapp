from backend.order_statuses import SETTLED_ORDER_PAYMENT_STATUSES
from backend.services.order_lifecycle_payment_state_contract import (
    enforce_settled_order_payment_state_contract,
)


def reconciliation():
    return {
        "schema_version": 1,
        "overall_status": "PENDING",
        "requires_operator_action": False,
        "stages": [
            {"key": "payment", "status": "PENDING", "reason": "payment_not_settled_yet", "next_action": "wait_for_payment_callback", "evidence": []},
            {"key": "inventory", "status": "PENDING", "reason": "inventory_not_expected_before_payment", "next_action": "wait_for_payment_callback", "evidence": []},
            {"key": "moysklad", "status": "PENDING", "reason": "moysklad_command_not_terminal_yet", "next_action": "wait_for_provider_command", "evidence": []},
            {"key": "fulfillment", "status": "PENDING", "reason": "fulfillment_not_expected_before_payment", "next_action": "wait_for_payment_callback", "evidence": []},
            {"key": "refunds", "status": "PENDING", "reason": "refund_in_progress", "next_action": "wait_for_refund_settlement", "evidence": []},
            {"key": "notifications", "status": "PENDING", "reason": "notification_delivery_in_progress", "next_action": "wait_for_notification_delivery", "evidence": []},
        ],
    }


def trace(payment_status, *, order_status="refund_requested", inventory=None, payments=None, delivery_status="pending"):
    return {
        "order": {
            "id": 42,
            "status": order_status,
            "payment_status": payment_status,
            "delivery_status": delivery_status,
            "total_amount": 1000.0,
        },
        "payments": payments if payments is not None else [{"status": "succeeded", "amount": 1000.0}],
        "inventory": inventory if inventory is not None else [
            {
                "kind": "commit",
                "quantity": 1,
                "stock_before": 5,
                "stock_after": 4,
                "reserved_before": 1,
                "reserved_after": 0,
            }
        ],
    }


def stage(result, key):
    return next(item for item in result["stages"] if item["key"] == key)


def test_contract_imports_all_canonical_refund_settlement_states():
    for status in {
        "refund_processing",
        "refund_pending",
        "refund_review_required",
        "partially_refunded",
        "refunded",
    }:
        assert status in SETTLED_ORDER_PAYMENT_STATUSES


def test_refund_pending_with_committed_inventory_is_post_settlement_not_prepayment():
    result = enforce_settled_order_payment_state_contract(
        reconciliation(),
        trace("refund_pending"),
    )

    assert stage(result, "payment")["status"] == "PASS"
    assert stage(result, "inventory")["status"] == "PASS"
    assert stage(result, "inventory")["reason"] == "inventory_commit_recorded"
    assert stage(result, "fulfillment")["reason"] == "paid_order_fulfillment_not_started"
    assert "before_payment" not in stage(result, "inventory")["reason"]
    assert "before_payment" not in stage(result, "fulfillment")["reason"]


def test_refund_review_keeps_payment_review_but_inventory_remains_settled():
    result = enforce_settled_order_payment_state_contract(
        reconciliation(),
        trace("refund_review_required"),
    )

    assert stage(result, "payment")["status"] == "REVIEW"
    assert stage(result, "payment")["reason"] == "payment_review_required"
    assert stage(result, "inventory")["status"] == "PASS"
    assert result["overall_status"] == "REVIEW"
    assert result["requires_operator_action"] is True


def test_partially_refunded_order_remains_post_settlement():
    result = enforce_settled_order_payment_state_contract(
        reconciliation(),
        trace("partially_refunded", order_status="partially_refunded"),
    )

    assert stage(result, "payment")["status"] == "PASS"
    assert stage(result, "inventory")["status"] == "PASS"
    assert stage(result, "inventory")["reason"] == "inventory_commit_recorded"


def test_settled_order_without_settled_payment_record_blocks_instead_of_waiting_for_payment():
    result = enforce_settled_order_payment_state_contract(
        reconciliation(),
        trace("refund_pending", payments=[]),
    )

    assert stage(result, "payment")["status"] == "BLOCKED"
    assert stage(result, "payment")["reason"] == "order_paid_without_settled_payment_record"
    assert result["overall_status"] == "BLOCKED"
    assert result["requires_operator_action"] is True


def test_refund_pending_without_inventory_commit_is_review_after_refund_request():
    result = enforce_settled_order_payment_state_contract(
        reconciliation(),
        trace("refund_pending", inventory=[]),
    )

    assert stage(result, "inventory")["status"] == "REVIEW"
    assert stage(result, "inventory")["reason"] == "paid_order_inventory_not_recorded"
    assert stage(result, "inventory")["next_action"] == "inspect_inventory_ledger"
    assert result["overall_status"] == "REVIEW"


def test_non_settled_order_state_is_left_untouched():
    source = reconciliation()
    result = enforce_settled_order_payment_state_contract(
        source,
        trace("pending", order_status="created", payments=[], inventory=[]),
    )

    assert result == source
