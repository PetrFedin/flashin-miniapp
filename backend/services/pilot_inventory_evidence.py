from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping

from sqlalchemy.orm import Session

from ..models import InventoryMovement, Order, OrderItem
from .inventory_movement_contract import (
    expected_core_chain,
    load_resalable_return_evidence,
    validate_variant_movement_chain,
)

INVENTORY_EVIDENCE_CONTRACT = 1
_STOCK_FIELDS = ("stock_before", "stock_after", "expected_stock_delta")


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _describe_chain_failure(
    failure: str,
    *,
    number: int,
    variant_id: int,
) -> str:
    messages = {
        "order_status_unsupported": "order status has no supported inventory contract",
        "movement_chain_missing": "inventory movement chain is missing",
        "movement_sequence_invalid": (
            "inventory movement sequence must contain the expected commercial chain "
            "followed only by physical returns"
        ),
        "core_quantity_mismatch": "commercial movement quantity does not match the order item",
        "movement_transition_invalid": "inventory movement transition is invalid",
        "movement_chain_not_contiguous": "inventory movement chain is not contiguous",
        "return_source_duplicate": "physical return movement source is duplicated",
        "physical_return_evidence_mismatch": (
            "sellable return movements do not exactly match resalable physical inspection evidence"
        ),
        "return_quantity_exceeds_commit": "sellable return quantity exceeds committed order quantity",
        "return_without_commit": "sellable return exists without a committed sale",
        "order_quantity_invalid": "order item quantity is invalid",
    }
    detail = messages.get(failure, f"inventory contract failure {failure}")
    return f"#{number}: variant {variant_id}: {detail}"


def validate_order_inventory_evidence(
    db: Session,
    record: Mapping[str, Any],
    order: Order,
) -> list[str]:
    """Validate a signed stock claim against commercial and physical truth.

    Contract v1 is intentionally retained. Financial refund state does not
    synthesize a stock return; `return` movements are accepted only when they
    exactly project durable `inspected/resalable` reverse-logistics events.
    """
    if not any(record.get(field) not in (None, "") for field in _STOCK_FIELDS):
        return []

    number = _as_int(record.get("number")) or 0
    errors: list[str] = []
    items = db.query(OrderItem).filter(OrderItem.order_id == order.id).all()
    expected_by_variant: dict[int, int] = defaultdict(int)
    for item in items:
        expected_by_variant[int(item.variant_id)] += int(item.quantity)
    if not expected_by_variant:
        return [f"#{number}: stock evidence requires PostgreSQL order items"]

    movements = (
        db.query(InventoryMovement)
        .filter(InventoryMovement.order_id == order.id)
        .order_by(InventoryMovement.id.asc())
        .all()
    )
    by_variant: dict[int, list[InventoryMovement]] = defaultdict(list)
    for movement in movements:
        by_variant[int(movement.variant_id)].append(movement)
    if set(by_variant) != set(expected_by_variant):
        errors.append(
            f"#{number}: inventory movement variants do not exactly match order items"
        )

    physical_returns = load_resalable_return_evidence(db, [int(order.id)])
    core_chain = expected_core_chain(str(order.status))
    total_stock_before = 0
    total_stock_after = 0
    for variant_id, expected_quantity in expected_by_variant.items():
        chain = by_variant.get(variant_id, [])
        failures = validate_variant_movement_chain(
            chain,
            order_quantity=expected_quantity,
            core_chain=core_chain,
            physical_returns=physical_returns.get((int(order.id), variant_id), ()),
        )
        errors.extend(
            _describe_chain_failure(failure, number=number, variant_id=variant_id)
            for failure in failures
        )
        if not chain:
            continue
        total_stock_before += int(chain[0].stock_before)
        total_stock_after += int(chain[-1].stock_after)

    signed_before = _as_int(record.get("stock_before"))
    signed_after = _as_int(record.get("stock_after"))
    signed_delta = _as_int(record.get("expected_stock_delta"))
    if signed_before != total_stock_before:
        errors.append(
            f"#{number}: signed stock_before does not match inventory movements"
        )
    if signed_after != total_stock_after:
        errors.append(
            f"#{number}: signed stock_after does not match inventory movements"
        )
    if signed_delta != total_stock_before - total_stock_after:
        errors.append(
            f"#{number}: signed expected_stock_delta does not match inventory movements"
        )
    return list(dict.fromkeys(errors))
