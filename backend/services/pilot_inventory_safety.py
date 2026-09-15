from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from sqlalchemy.orm import Session

from ..models import (
    InventoryMovement,
    Order,
    OrderItem,
    ProductVariant,
    StockReconciliationLog,
)
from .inventory_movement_contract import (
    expected_core_chain,
    load_resalable_return_evidence,
    validate_variant_movement_chain,
)


def _latest_reconciliation_by_variant(
    db: Session,
    variant_ids: set[int],
) -> dict[int, StockReconciliationLog]:
    if not variant_ids:
        return {}
    rows = (
        db.query(StockReconciliationLog)
        .filter(StockReconciliationLog.variant_id.in_(sorted(variant_ids)))
        .order_by(
            StockReconciliationLog.variant_id.asc(),
            StockReconciliationLog.created_at.desc(),
            StockReconciliationLog.id.desc(),
        )
        .all()
    )
    latest: dict[int, StockReconciliationLog] = {}
    for row in rows:
        latest.setdefault(int(row.variant_id), row)
    return latest


def build_pilot_inventory_safety(
    db: Session,
    order_ids: Iterable[int],
) -> dict[str, Any]:
    """Evaluate inventory invariants for the exact accepted pilot orders.

    Financial refund state has no authority over sellable-return stock. Any
    ledger `return` must instead match a durable resalable physical-inspection
    event by order, variant, source and quantity.

    The result intentionally contains only bounded codes and aggregate counts.
    It never exposes order IDs, variant IDs, SKUs, provider values or raw errors.
    """

    normalized_order_ids = sorted({int(value) for value in order_ids})
    blocking_codes: list[str] = []
    chain_failures = 0

    if not normalized_order_ids:
        return {
            "healthy": True,
            "blocking_codes": [],
            "pilot_orders": 0,
            "pilot_variants": 0,
            "open_reconciliation_variants": 0,
            "chain_failures": 0,
            "stop_reason": None,
        }

    orders = (
        db.query(Order)
        .filter(Order.id.in_(normalized_order_ids))
        .order_by(Order.id.asc())
        .all()
    )
    if len(orders) != len(normalized_order_ids):
        blocking_codes.append("inventory_pilot_order_missing")

    items = (
        db.query(OrderItem)
        .filter(OrderItem.order_id.in_(normalized_order_ids))
        .order_by(OrderItem.order_id.asc(), OrderItem.variant_id.asc(), OrderItem.id.asc())
        .all()
    )
    expected_by_order: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for item in items:
        quantity = int(item.quantity)
        if quantity <= 0:
            blocking_codes.append("inventory_order_item_invalid")
            continue
        expected_by_order[int(item.order_id)][int(item.variant_id)] += quantity

    for order in orders:
        if not expected_by_order.get(int(order.id)):
            blocking_codes.append("inventory_order_items_missing")

    variant_ids = {
        variant_id
        for order_items in expected_by_order.values()
        for variant_id in order_items
    }
    variants = (
        db.query(ProductVariant)
        .filter(ProductVariant.id.in_(sorted(variant_ids)))
        .order_by(ProductVariant.id.asc())
        .all()
        if variant_ids
        else []
    )
    variants_by_id = {int(variant.id): variant for variant in variants}
    if set(variants_by_id) != variant_ids:
        blocking_codes.append("inventory_variant_missing")
    for variant in variants:
        stock = int(variant.stock_qty)
        reserved = int(variant.reserved_qty)
        if stock < 0 or reserved < 0 or reserved > stock:
            blocking_codes.append("inventory_variant_balance_invalid")

    movements = (
        db.query(InventoryMovement)
        .filter(InventoryMovement.order_id.in_(normalized_order_ids))
        .order_by(
            InventoryMovement.order_id.asc(),
            InventoryMovement.variant_id.asc(),
            InventoryMovement.id.asc(),
        )
        .all()
    )
    movements_by_order: dict[int, dict[int, list[InventoryMovement]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for movement in movements:
        movements_by_order[int(movement.order_id)][int(movement.variant_id)].append(movement)

    physical_returns = load_resalable_return_evidence(db, normalized_order_ids)
    for order in orders:
        order_id = int(order.id)
        expected = expected_by_order.get(order_id, {})
        actual = movements_by_order.get(order_id, {})
        if set(actual) != set(expected):
            blocking_codes.append("inventory_movement_variant_mismatch")
            chain_failures += 1

        core_chain = expected_core_chain(str(order.status))
        if core_chain is None:
            blocking_codes.append("inventory_order_status_unsupported")
            chain_failures += 1
        for variant_id, expected_quantity in expected.items():
            failures = validate_variant_movement_chain(
                actual.get(variant_id, []),
                order_quantity=expected_quantity,
                core_chain=core_chain,
                physical_returns=physical_returns.get((order_id, variant_id), ()),
            )
            if failures:
                blocking_codes.append("inventory_movement_chain_invalid")
                if "physical_return_evidence_mismatch" in failures:
                    blocking_codes.append("inventory_physical_return_evidence_mismatch")
                if "return_quantity_exceeds_commit" in failures:
                    blocking_codes.append("inventory_physical_return_over_commit")
                chain_failures += 1

    latest_reconciliation = _latest_reconciliation_by_variant(db, variant_ids)
    open_reconciliation_variants = 0
    for row in latest_reconciliation.values():
        status = str(row.status or "").strip().lower()
        if status == "resolved":
            continue
        if status == "open" and int(row.local_stock_qty) != int(row.external_stock_qty):
            open_reconciliation_variants += 1
            blocking_codes.append("inventory_reconciliation_open")
            continue
        if status not in {"open", "resolved"}:
            blocking_codes.append("inventory_reconciliation_status_invalid")

    blocking_codes = sorted(set(blocking_codes))
    healthy = not blocking_codes
    return {
        "healthy": healthy,
        "blocking_codes": blocking_codes,
        "pilot_orders": len(normalized_order_ids),
        "pilot_variants": len(variant_ids),
        "open_reconciliation_variants": open_reconciliation_variants,
        "chain_failures": chain_failures,
        "stop_reason": None if healthy else "pilot_inventory_integrity_failure",
    }
