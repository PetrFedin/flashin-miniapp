from backend.services.order_lifecycle_moysklad_contract import enforce_moysklad_lifecycle_contract


def reconciliation(status="PASS"):
    return {
        "schema_version": 1,
        "overall_status": status,
        "requires_operator_action": status in {"REVIEW", "BLOCKED"},
        "stages": [
            {"key": "payment", "status": "PASS", "reason": "payment_settled", "next_action": "none", "evidence": []},
            {"key": "inventory", "status": "PASS", "reason": "inventory_commit_recorded", "next_action": "none", "evidence": []},
            {"key": "moysklad", "status": status, "reason": "moysklad_commands_terminal_success", "next_action": "none", "evidence": []},
            {"key": "fulfillment", "status": "PASS", "reason": "fulfillment_completed", "next_action": "none", "evidence": []},
            {"key": "refunds", "status": "PASS", "reason": "no_refund_requested", "next_action": "none", "evidence": []},
            {"key": "notifications", "status": "PASS", "reason": "notifications_delivered", "next_action": "none", "evidence": []},
        ],
    }


def trace(
    status,
    payment_status,
    delivery_status,
    command_types,
    *,
    physical_returns=None,
    physical_command_case_ids=None,
):
    physical_ids = iter(list(physical_command_case_ids or []))
    commands = []
    for command_type in command_types:
        item = {"provider": "moysklad", "command_type": command_type, "status": "sent"}
        if command_type == "moysklad.physical_sales_return.create":
            try:
                case_id = next(physical_ids)
            except StopIteration:
                case_id = None
            if case_id is not None:
                item.update({
                    "aggregate_type": "return_logistics_case",
                    "aggregate_id": str(case_id),
                })
        commands.append(item)
    return {
        "order": {
            "status": status,
            "payment_status": payment_status,
            "delivery_status": delivery_status,
        },
        "physical_returns": list(physical_returns or []),
        "provider_commands": commands,
    }


def moysklad_stage(result):
    return next(item for item in result["stages"] if item["key"] == "moysklad")


def test_paid_order_missing_customer_order_remains_pending_not_green():
    result = enforce_moysklad_lifecycle_contract(
        reconciliation("PASS"),
        trace("paid", "paid", "pending", []),
    )

    assert result["overall_status"] == "PENDING"
    assert result["requires_operator_action"] is False
    assert moysklad_stage(result)["reason"] == "moysklad_required_command_missing"
    assert "moysklad.customer_order.create" in moysklad_stage(result)["evidence"][0]


def test_refund_pending_still_requires_original_customer_order_command():
    result = enforce_moysklad_lifecycle_contract(
        reconciliation("PASS"),
        trace("refund_requested", "refund_pending", "pending", []),
    )

    assert result["overall_status"] == "PENDING"
    assert result["requires_operator_action"] is False
    assert moysklad_stage(result)["status"] == "PENDING"
    assert "moysklad.customer_order.create" in moysklad_stage(result)["evidence"][0]


def test_shipped_order_missing_demand_requires_review():
    result = enforce_moysklad_lifecycle_contract(
        reconciliation("PASS"),
        trace("shipped", "paid", "shipped", ["moysklad.customer_order.create"]),
    )

    assert result["overall_status"] == "REVIEW"
    assert result["requires_operator_action"] is True
    assert moysklad_stage(result)["status"] == "REVIEW"
    assert "moysklad.demand.create" in moysklad_stage(result)["evidence"][0]


def test_financially_refunded_order_does_not_require_physical_sales_return_without_physical_evidence():
    result = enforce_moysklad_lifecycle_contract(
        reconciliation("PASS"),
        trace(
            "refunded",
            "refunded",
            "delivered",
            ["moysklad.customer_order.create", "moysklad.demand.create"],
        ),
    )

    assert result["overall_status"] == "PASS"
    assert result["requires_operator_action"] is False
    assert moysklad_stage(result)["status"] == "PASS"


def test_inspected_physical_return_requires_physical_sales_return_command():
    result = enforce_moysklad_lifecycle_contract(
        reconciliation("PASS"),
        trace(
            "refunded",
            "refunded",
            "delivered",
            ["moysklad.customer_order.create", "moysklad.demand.create"],
            physical_returns=[{"id": 7, "status": "inspected"}],
        ),
    )

    assert result["overall_status"] == "REVIEW"
    assert result["requires_operator_action"] is True
    evidence = " ".join(moysklad_stage(result)["evidence"])
    assert "moysklad.physical_sales_return.create" in evidence
    assert "missing_case_ids=7" in evidence
    assert "moysklad.sales_return.create" not in evidence


def test_inspected_physical_return_with_full_moysklad_lifecycle_stays_pass():
    result = enforce_moysklad_lifecycle_contract(
        reconciliation("PASS"),
        trace(
            "refunded",
            "refunded",
            "delivered",
            [
                "moysklad.customer_order.create",
                "moysklad.demand.create",
                "moysklad.physical_sales_return.create",
            ],
            physical_returns=[{"id": 7, "status": "inspected"}],
            physical_command_case_ids=[7],
        ),
    )

    assert result["overall_status"] == "PASS"
    assert result["requires_operator_action"] is False
    assert moysklad_stage(result)["status"] == "PASS"


def test_each_inspected_physical_case_requires_its_own_provider_command():
    result = enforce_moysklad_lifecycle_contract(
        reconciliation("PASS"),
        trace(
            "refunded",
            "refunded",
            "delivered",
            [
                "moysklad.customer_order.create",
                "moysklad.demand.create",
                "moysklad.physical_sales_return.create",
            ],
            physical_returns=[
                {"id": 7, "status": "inspected"},
                {"id": 8, "status": "inspected"},
            ],
            physical_command_case_ids=[7],
        ),
    )

    assert result["overall_status"] == "REVIEW"
    assert result["requires_operator_action"] is True
    assert "missing_case_ids=8" in " ".join(moysklad_stage(result)["evidence"])


def test_two_inspected_physical_cases_with_two_commands_stay_pass():
    result = enforce_moysklad_lifecycle_contract(
        reconciliation("PASS"),
        trace(
            "refunded",
            "refunded",
            "delivered",
            [
                "moysklad.customer_order.create",
                "moysklad.demand.create",
                "moysklad.physical_sales_return.create",
                "moysklad.physical_sales_return.create",
            ],
            physical_returns=[
                {"id": 7, "status": "inspected"},
                {"id": 8, "status": "inspected"},
            ],
            physical_command_case_ids=[7, 8],
        ),
    )

    assert result["overall_status"] == "PASS"
    assert result["requires_operator_action"] is False
    assert moysklad_stage(result)["status"] == "PASS"


def test_physical_return_in_progress_does_not_require_sales_return_early():
    result = enforce_moysklad_lifecycle_contract(
        reconciliation("PASS"),
        trace(
            "refunded",
            "refunded",
            "delivered",
            ["moysklad.customer_order.create", "moysklad.demand.create"],
            physical_returns=[{"id": 7, "status": "received"}],
        ),
    )

    assert result["overall_status"] == "PASS"
    assert moysklad_stage(result)["status"] == "PASS"


def test_existing_blocked_provider_stage_is_never_downgraded():
    source = reconciliation("BLOCKED")
    result = enforce_moysklad_lifecycle_contract(
        source,
        trace("refunded", "refunded", "delivered", []),
    )

    assert result["overall_status"] == "BLOCKED"
    assert result["requires_operator_action"] is True
    assert moysklad_stage(result)["status"] == "BLOCKED"
