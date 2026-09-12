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

_COMMITTED_ORDER_STATUSES = {
    "paid",
    "picking",
    "packed",
    "ready",
    "shipped",
    "completed",
    "refund_pending",
    "partially_refunded",
    "refund_retry_required",
    "refund_review_required",
}
_CANCELLED_ORDER_STATUSES = {"cancelled", "expired"}
_REFUNDED_ORDER_STATUSES = {"refunded"}


def _movement_transition_valid(movement: InventoryMovement) -> bool:
    quantity = int(movement.quantity)
    stock_before = int(movement.stock_before)
    stock_after = int(movement.stock_after)
    reserved_before = int(movement.reserved_before)
    reserved_after = int(movement.reserved_after)
    if quantity <= 0 or min(stock_before, stock_after, reserved_before, reserved_after) < 0:
        return False
    if reserved_before > stock_before or reserved_after > stock_after:
        return False
    if movement.kind == "reserve":
        return stock_after == stock_before and reserved_after == reserved_before + quantity
    if movement.kind == "release":
        return stock_after == stock_before and reserved_after == reserved_before - quantity
    if movement.kind == "commit":
        return (
            stock_after == stock_before - quantity
            and reserved_after == reserved_before - quantity
        )
    if movement.kind == "return":
        return stock_after == stock_before + quantity and reserved_after == reserved_before
    return False


def _expected_chain(order_status: str) -> tuple[str, ...] | None:
    status = str(order_status or "").strip().lower()
    if status in _CANCELLED_ORDER_STATUSES:
        return ("reserve", "release")
    if status in _REFUNDED_ORDER_STATUSES:
        return ("reserve", "commit", "return")
    if status in _COMMITTED_ORDER_STATUSES:
        return ("reserve", "commit")
    if status in {"created", "pending", "pending_payment", "payment_pending"}:
        return ("reserve",)
    return None


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
        .order_by(InventoryMovement.order_id.asc(), InventoryMovement.variant_id.asc(), InventoryMovement.id.asc())
        .all()
    )
    movements_by_order: dict[int, dict[int, list[InventoryMovement]]] = defaultdict(lambda: defaultdict(list))
    for movement in movements:
        movements_by_order[int(movement.order_id)][int(movement.variant_id)].append(movement)

    for order in orders:
        order_id = int(order.id)
        expected = expected_by_order.get(order_id, {})
        actual = movements_by_order.get(order_id, {})
        if set(actual) != set(expected):
            blocking_codes.append("inventory_movement_variant_mismatch")
            chain_failures += 1
        expected_chain = _expected_chain(str(order.status))
        if expected_chain is None:
            blocking_codes.append("inventory_order_status_unsupported")
            chain_failures += 1
        for variant_id, expected_quantity in expected.items():
            chain = actual.get(variant_id, [])
            kinds = tuple(str(movement.kind) for movement in chain)
            valid = bool(chain) and expected_chain is not None and kinds == expected_chain
            if any(int(movement.quantity) != expected_quantity for movement in chain):
                valid = False
            if any(not _movement_transition_valid(movement) for movement in chain):
                valid = False
            if not valid:
                blocking_codes.append("inventory_movement_chain_invalid")
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
