import json

from backend.services.order_lifecycle_reconciliation import evaluate_order_lifecycle


def trace(**overrides):
    payload = {
        "order": {
            "id": 42,
            "customer_id": 7,
            "status": "created",
            "payment_status": "pending",
            "delivery_status": "pending",
            "total_amount": 1000.0,
        },
        "payments": [],
        "payment_events": [],
        "returns": [],
        "provider_commands": [],
        "inventory": [],
        "fulfillment": [],
        "business_events": [],
        "notifications": [],
        "sla": [],
        "attention": {"overdue_sla": 0},
    }
    payload.update(overrides)
    return payload


def stage(result, key):
    return next(item for item in result["stages"] if item["key"] == key)


def settled_trace():
    return trace(
        order={
            "id": 42,
            "customer_id": 7,
            "status": "completed",
            "payment_status": "paid",
            "delivery_status": "delivered",
            "total_amount": 1000.0,
        },
        payments=[{"status": "succeeded", "amount": 1000.0}],
        provider_commands=[
            {
                "provider": "moysklad",
                "command_type": "moysklad.customer_order.create",
                "status": "sent",
            },
            {
                "provider": "moysklad",
                "command_type": "moysklad.demand.create",
                "status": "sent",
            },
        ],
        inventory=[
            {
                "kind": "reserve",
                "quantity": 1,
                "stock_before": 5,
                "stock_after": 5,
                "reserved_before": 0,
                "reserved_after": 1,
            },
            {
                "kind": "commit",
                "quantity": 1,
                "stock_before": 5,
                "stock_after": 4,
                "reserved_before": 1,
                "reserved_after": 0,
            },
        ],
        fulfillment=[{"status": "packed"}],
        notifications=[{"status": "sent"}],
    )


def test_fresh_order_is_pending_without_operator_action():
    result = evaluate_order_lifecycle(trace())

    assert result["overall_status"] == "PENDING"
    assert result["requires_operator_action"] is False
    assert [item["key"] for item in result["stages"]] == [
        "payment",
        "inventory",
        "moysklad",
        "fulfillment",
        "refunds",
        "notifications",
    ]


def test_completed_coherent_order_is_pass():
    result = evaluate_order_lifecycle(settled_trace())

    assert result["overall_status"] == "PASS"
    assert result["requires_operator_action"] is False
    assert {item["status"] for item in result["stages"]} == {"PASS"}


def test_open_payment_review_is_review_not_blocked():
    payload = trace(
        order={
            "id": 42,
            "status": "paid_review_required",
            "payment_status": "paid_review_required",
            "delivery_status": "pending",
            "total_amount": 1000.0,
        },
        payments=[{"status": "succeeded", "amount": 1000.0}],
    )

    result = evaluate_order_lifecycle(payload)

    assert result["overall_status"] == "REVIEW"
    assert result["requires_operator_action"] is True
    assert stage(result, "payment")["next_action"] == "inspect_payment_review"


def test_failed_moysklad_command_blocks_reconciliation():
    payload = settled_trace()
    payload["provider_commands"] = [
        {
            "provider": "moysklad",
            "command_type": "moysklad.customer_order.create",
            "status": "failed",
        }
    ]

    result = evaluate_order_lifecycle(payload)

    assert result["overall_status"] == "BLOCKED"
    assert result["requires_operator_action"] is True
    assert stage(result, "moysklad")["status"] == "BLOCKED"


def test_invalid_inventory_ledger_blocks_reconciliation():
    payload = settled_trace()
    payload["inventory"] = [
        {
            "kind": "commit",
            "quantity": 1,
            "stock_before": 1,
            "stock_after": -1,
            "reserved_before": 1,
            "reserved_after": 0,
        }
    ]

    result = evaluate_order_lifecycle(payload)

    assert result["overall_status"] == "BLOCKED"
    assert stage(result, "inventory")["reason"] == "inventory_ledger_invalid"


def test_refund_review_requires_operator_review():
    payload = settled_trace()
    payload["returns"] = [
        {
            "status": "refund_review_required",
            "provider_refund_id": "refund-safe-id",
            "refund_amount": 1000.0,
        }
    ]

    result = evaluate_order_lifecycle(payload)

    assert result["overall_status"] == "REVIEW"
    assert stage(result, "refunds")["next_action"] == "inspect_refund_reconciliation"


def test_failed_notification_requires_review_but_does_not_block_money_state():
    payload = settled_trace()
    payload["notifications"] = [{"status": "failed"}]

    result = evaluate_order_lifecycle(payload)

    assert result["overall_status"] == "REVIEW"
    assert stage(result, "notifications")["status"] == "REVIEW"


def test_pending_provider_command_is_normal_progress_without_operator_action():
    payload = trace(
        order={
            "id": 42,
            "status": "paid",
            "payment_status": "paid",
            "delivery_status": "pending",
            "total_amount": 1000.0,
        },
        payments=[{"status": "succeeded", "amount": 1000.0}],
        provider_commands=[
            {
                "provider": "moysklad",
                "command_type": "moysklad.customer_order.create",
                "status": "processing",
            }
        ],
        inventory=[
            {
                "kind": "reserve",
                "quantity": 1,
                "stock_before": 5,
                "stock_after": 5,
                "reserved_before": 0,
                "reserved_after": 1,
            }
        ],
        fulfillment=[{"status": "pick_pending"}],
        notifications=[{"status": "pending"}],
    )

    result = evaluate_order_lifecycle(payload)

    assert result["overall_status"] == "PENDING"
    assert result["requires_operator_action"] is False
    assert stage(result, "moysklad")["status"] == "PENDING"


def test_unpaid_cancelled_order_passes_when_cancellation_notification_is_delivered():
    payload = trace(
        order={
            "id": 42,
            "status": "cancelled",
            "payment_status": "cancelled",
            "delivery_status": "cancelled",
            "total_amount": 1000.0,
        },
        payments=[{"status": "canceled", "amount": 1000.0}],
        notifications=[{"status": "sent"}],
    )

    result = evaluate_order_lifecycle(payload)

    assert result["overall_status"] == "PASS"
    assert result["requires_operator_action"] is False


def test_cancelled_order_without_notification_evidence_requires_review():
    payload = trace(
        order={
            "id": 42,
            "status": "cancelled",
            "payment_status": "cancelled",
            "delivery_status": "cancelled",
            "total_amount": 1000.0,
        },
        payments=[{"status": "canceled", "amount": 1000.0}],
    )

    result = evaluate_order_lifecycle(payload)

    assert result["overall_status"] == "REVIEW"
    assert result["requires_operator_action"] is True
    assert stage(result, "notifications")["reason"] == "settled_order_has_no_notification_evidence"


def test_sensitive_trace_extras_are_not_reflected_in_reconciliation_output():
    payload = settled_trace()
    payload["provider_commands"][0].update(
        {
            "payload_json": '{"token":"never-emit"}',
            "idempotency_key": "idem-never-emit",
            "last_error": "provider-secret-error",
        }
    )
    payload["notifications"][0].update(
        {
            "telegram_id": 999999,
            "text": "private notification body",
        }
    )

    output = json.dumps(evaluate_order_lifecycle(payload), sort_keys=True)

    assert "never-emit" not in output
    assert "idem-never-emit" not in output
    assert "provider-secret-error" not in output
    assert "999999" not in output
    assert "private notification body" not in output
