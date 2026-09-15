from pathlib import Path

from backend.services.order_lifecycle_physical_inventory_contract import (
    enforce_physical_inventory_variant_contract,
)


ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "backend" / "api" / "ops.py"


def reconciliation(status="PASS"):
    return {
        "schema_version": 1,
        "overall_status": status,
        "requires_operator_action": status in {"REVIEW", "BLOCKED"},
        "stages": [
            {"key": "payment", "status": "PASS", "reason": "payment_settled", "next_action": "none", "evidence": []},
            {"key": "inventory", "status": status, "reason": "physical_return_inventory_exact", "next_action": "none", "evidence": []},
            {"key": "moysklad", "status": "PASS", "reason": "moysklad_commands_terminal_success", "next_action": "none", "evidence": []},
        ],
    }


def trace(*, physical_items=None, inventory_returns=None):
    return {
        "physical_returns": [
            {
                "id": 7,
                "status": "inspected",
                "items": list(physical_items or []),
            }
        ] if physical_items is not None else [],
        "inventory": [
            {
                "kind": "return",
                "quantity": quantity,
                **({"variant_id": variant_id} if variant_id is not None else {}),
            }
            for variant_id, quantity in (inventory_returns or [])
        ],
    }


def inventory_stage(result):
    return next(item for item in result["stages"] if item["key"] == "inventory")


def test_matching_variant_evidence_stays_pass():
    result = enforce_physical_inventory_variant_contract(
        reconciliation(),
        trace(
            physical_items=[{"variant_id": 101, "resalable_qty": 1}],
            inventory_returns=[(101, 1)],
        ),
    )

    assert result["overall_status"] == "PASS"
    assert result["requires_operator_action"] is False
    assert inventory_stage(result)["status"] == "PASS"


def test_equal_total_on_wrong_variant_is_blocked():
    result = enforce_physical_inventory_variant_contract(
        reconciliation(),
        trace(
            physical_items=[{"variant_id": 101, "resalable_qty": 1}],
            inventory_returns=[(202, 1)],
        ),
    )

    assert result["overall_status"] == "BLOCKED"
    assert result["requires_operator_action"] is True
    stage = inventory_stage(result)
    assert stage["reason"] == "inventory_physical_return_variant_mismatch"
    assert "physical.resalable_by_variant=101:1" in stage["evidence"]
    assert "inventory.return_by_variant=202:1" in stage["evidence"]


def test_multiple_variants_require_exact_distribution_not_only_total():
    result = enforce_physical_inventory_variant_contract(
        reconciliation(),
        trace(
            physical_items=[
                {"variant_id": 101, "resalable_qty": 2},
                {"variant_id": 202, "resalable_qty": 1},
            ],
            inventory_returns=[(101, 1), (202, 2)],
        ),
    )

    assert result["overall_status"] == "BLOCKED"
    stage = inventory_stage(result)
    assert "physical.resalable_by_variant=101:2,202:1" in stage["evidence"]
    assert "inventory.return_by_variant=101:1,202:2" in stage["evidence"]


def test_multiple_movements_for_same_variant_are_summed_exactly():
    result = enforce_physical_inventory_variant_contract(
        reconciliation(),
        trace(
            physical_items=[{"variant_id": 101, "resalable_qty": 2}],
            inventory_returns=[(101, 1), (101, 1)],
        ),
    )

    assert result["overall_status"] == "PASS"
    assert inventory_stage(result)["status"] == "PASS"


def test_positive_resalable_quantity_without_variant_id_is_blocked():
    result = enforce_physical_inventory_variant_contract(
        reconciliation(),
        trace(
            physical_items=[{"resalable_qty": 1}],
            inventory_returns=[(101, 1)],
        ),
    )

    assert result["overall_status"] == "BLOCKED"
    assert inventory_stage(result)["reason"] == "physical_return_variant_evidence_invalid"
    assert "physical.variant_evidence_valid=false" in inventory_stage(result)["evidence"]


def test_return_movement_without_variant_id_is_blocked():
    result = enforce_physical_inventory_variant_contract(
        reconciliation(),
        trace(
            physical_items=[{"variant_id": 101, "resalable_qty": 1}],
            inventory_returns=[(None, 1)],
        ),
    )

    assert result["overall_status"] == "BLOCKED"
    assert inventory_stage(result)["reason"] == "physical_return_variant_evidence_invalid"
    assert "inventory.variant_evidence_valid=false" in inventory_stage(result)["evidence"]


def test_existing_inventory_block_is_never_downgraded_or_rewritten():
    source = reconciliation("BLOCKED")
    source_stage = inventory_stage(source)
    source_stage["reason"] = "inventory_ledger_invalid"
    source_stage["evidence"] = ["inventory.invalid_rows=1"]

    result = enforce_physical_inventory_variant_contract(
        source,
        trace(
            physical_items=[{"variant_id": 101, "resalable_qty": 1}],
            inventory_returns=[(202, 1)],
        ),
    )

    assert result["overall_status"] == "BLOCKED"
    assert inventory_stage(result)["reason"] == "inventory_ledger_invalid"
    assert inventory_stage(result)["evidence"] == ["inventory.invalid_rows=1"]


def test_ops_trace_applies_variant_contract_before_provider_contract():
    source = OPS.read_text(encoding="utf-8")
    import_fragment = "from ..services.order_lifecycle_physical_inventory_contract import enforce_physical_inventory_variant_contract"
    physical_call = "enforce_physical_inventory_variant_contract(reconciliation, trace)"
    provider_call = "enforce_moysklad_lifecycle_contract(reconciliation, trace)"

    assert import_fragment in source
    assert physical_call in source
    assert provider_call in source
    assert source.index(physical_call) < source.index(provider_call)
