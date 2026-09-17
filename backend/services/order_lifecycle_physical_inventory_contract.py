from __future__ import annotations

from copy import deepcopy
from typing import Any


_STATUS_RANK = {"PASS": 0, "PENDING": 1, "REVIEW": 2, "BLOCKED": 3}


def _status(value: Any) -> str:
    return str(value or "").strip().lower()


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _physical_resalable_by_variant(trace: dict[str, Any]) -> tuple[dict[int, int], bool]:
    totals: dict[int, int] = {}
    physical_returns = trace.get("physical_returns")
    if not isinstance(physical_returns, list):
        return totals, True

    for case in physical_returns:
        if not isinstance(case, dict):
            continue
        items = case.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                quantity = int(item.get("resalable_qty") or 0)
            except (TypeError, ValueError):
                return {}, False
            if quantity < 0:
                return {}, False
            if quantity == 0:
                continue
            variant_id = _positive_int(item.get("variant_id"))
            if variant_id is None:
                return {}, False
            totals[variant_id] = totals.get(variant_id, 0) + quantity
    return totals, True


def _inventory_kind_by_variant(
    trace: dict[str, Any],
    kind: str,
) -> tuple[dict[int, int], bool]:
    totals: dict[int, int] = {}
    inventory = trace.get("inventory")
    if not isinstance(inventory, list):
        return totals, True

    for movement in inventory:
        if not isinstance(movement, dict) or _status(movement.get("kind")) != kind:
            continue
        try:
            quantity = int(movement.get("quantity") or 0)
        except (TypeError, ValueError):
            return {}, False
        if quantity <= 0:
            return {}, False
        variant_id = _positive_int(movement.get("variant_id"))
        if variant_id is None:
            return {}, False
        totals[variant_id] = totals.get(variant_id, 0) + quantity
    return totals, True


def _strictest_stage_status(stages: list[dict[str, Any]]) -> str:
    result = "PASS"
    for item in stages:
        if not isinstance(item, dict):
            continue
        candidate = str(item.get("status") or "REVIEW")
        if candidate not in _STATUS_RANK:
            candidate = "REVIEW"
        if _STATUS_RANK[candidate] > _STATUS_RANK[result]:
            result = candidate
    return result


def _evidence(prefix: str, values: dict[int, int]) -> str:
    if not values:
        return f"{prefix}=none"
    rendered = ",".join(f"{variant_id}:{values[variant_id]}" for variant_id in sorted(values))
    return f"{prefix}={rendered}"


def _block(
    inventory_stage: dict[str, Any],
    *,
    reason: str,
    evidence: list[str],
) -> None:
    inventory_stage.update(
        {
            "status": "BLOCKED",
            "reason": reason,
            "next_action": "inspect_inventory_ledger",
            "evidence": evidence,
        }
    )


def enforce_physical_inventory_variant_contract(
    reconciliation: dict[str, Any],
    trace: dict[str, Any],
) -> dict[str, Any]:
    """Require physical-return inventory evidence to reconcile per variant.

    Aggregate quantity equality is insufficient: a return movement for variant B
    must never compensate for missing inventory evidence for physically returned
    variant A. A sellable return also cannot exceed the quantity previously
    committed for that exact variant. The trace contains only sanitized ids and
    quantities, so the contract emits no provider payloads or customer data.
    """

    result = deepcopy(reconciliation)
    stages = result.get("stages") if isinstance(result.get("stages"), list) else []
    inventory_stage = next(
        (item for item in stages if isinstance(item, dict) and item.get("key") == "inventory"),
        None,
    )
    if inventory_stage is None or inventory_stage.get("status") == "BLOCKED":
        return result

    physical_by_variant, physical_valid = _physical_resalable_by_variant(trace)
    return_by_variant, return_valid = _inventory_kind_by_variant(trace, "return")

    if not physical_valid or not return_valid:
        _block(
            inventory_stage,
            reason="physical_return_variant_evidence_invalid",
            evidence=[
                f"physical.variant_evidence_valid={str(physical_valid).lower()}",
                f"inventory.return_variant_evidence_valid={str(return_valid).lower()}",
            ],
        )
    elif physical_by_variant != return_by_variant:
        _block(
            inventory_stage,
            reason="inventory_physical_return_variant_mismatch",
            evidence=[
                _evidence("physical.resalable_by_variant", physical_by_variant),
                _evidence("inventory.return_by_variant", return_by_variant),
            ],
        )
    elif return_by_variant:
        commit_by_variant, commit_valid = _inventory_kind_by_variant(trace, "commit")
        if not commit_valid:
            _block(
                inventory_stage,
                reason="physical_return_variant_evidence_invalid",
                evidence=["inventory.commit_variant_evidence_valid=false"],
            )
        else:
            exceeds_commit = {
                variant_id: quantity
                for variant_id, quantity in return_by_variant.items()
                if quantity > commit_by_variant.get(variant_id, 0)
            }
            if exceeds_commit:
                _block(
                    inventory_stage,
                    reason="inventory_return_exceeds_committed_variant_quantity",
                    evidence=[
                        _evidence("inventory.return_by_variant", return_by_variant),
                        _evidence("inventory.commit_by_variant", commit_by_variant),
                    ],
                )
            else:
                return result
    else:
        return result

    overall = _strictest_stage_status(stages)
    supplied = str(result.get("overall_status") or "REVIEW")
    if supplied not in _STATUS_RANK:
        supplied = "REVIEW"
    if _STATUS_RANK[supplied] > _STATUS_RANK[overall]:
        overall = supplied
    result["overall_status"] = overall
    result["requires_operator_action"] = True
    return result
