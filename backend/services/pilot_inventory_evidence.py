from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping

from sqlalchemy.orm import Session

from ..models import InventoryMovement, Order, OrderItem

INVENTORY_EVIDENCE_CONTRACT = 1
_STOCK_FIELDS = ("stock_before", "stock_after", "expected_stock_delta")
_COMMITTED_ORDER_STATUSES = {
    "paid",
    "picking",
    "packed",
    "ready",
    "shipped",
    "completed",
    "partially_refunded",
    "refunded",
}


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _validate_transition(
    movement: InventoryMovement,
    *,
    number: int,
) -> list[str]:
    errors: list[str] = []
    quantity = int(movement.quantity)
    before_stock = int(movement.stock_before)
    after_stock = int(movement.stock_after)
    before_reserved = int(movement.reserved_before)
    after_reserved = int(movement.reserved_after)
    if quantity <= 0:
        errors.append(f"#{number}: inventory movement quantity is not positive")
    if min(before_stock, after_stock, before_reserved, after_reserved) < 0:
        errors.append(f"#{number}: inventory movement contains negative quantities")
    if movement.kind == "reserve":
        if after_stock != before_stock or after_reserved != before_reserved + quantity:
            errors.append(f"#{number}: reserve inventory transition is invalid")
    elif movement.kind == "release":
        if after_stock != before_stock or after_reserved != before_reserved - quantity:
            errors.append(f"#{number}: release inventory transition is invalid")
    elif movement.kind == "commit":
        if (
            after_stock != before_stock - quantity
            or after_reserved != before_reserved - quantity
        ):
            errors.append(f"#{number}: commit inventory transition is invalid")
    else:
        errors.append(f"#{number}: unsupported inventory movement kind {movement.kind!r}")
    return errors


def validate_order_inventory_evidence(
    db: Session,
    record: Mapping[str, Any],
    order: Order,
) -> list[str]:
    """Validate a signed stock claim against the order-linked movement chain."""
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

    total_stock_before = 0
    total_stock_after = 0
    for variant_id, expected_quantity in expected_by_variant.items():
        chain = by_variant.get(variant_id, [])
        if not chain:
            errors.append(
                f"#{number}: missing inventory movement chain for variant {variant_id}"
            )
            continue
        kinds = [movement.kind for movement in chain]
        if kinds not in (["reserve"], ["reserve", "release"], ["reserve", "commit"]):
            errors.append(
                f"#{number}: inventory movement sequence for variant {variant_id} "
                f"must be reserve, reserve/release or reserve/commit"
            )
        if any(int(movement.quantity) != expected_quantity for movement in chain):
            errors.append(
                f"#{number}: inventory movement quantity for variant {variant_id} "
                "does not match the order item"
            )
        for index, movement in enumerate(chain):
            errors.extend(_validate_transition(movement, number=number))
            if index:
                previous = chain[index - 1]
                if (
                    int(previous.stock_after) != int(movement.stock_before)
                    or int(previous.reserved_after) != int(movement.reserved_before)
                ):
                    errors.append(
                        f"#{number}: inventory movement chain for variant {variant_id} "
                        "is not contiguous"
                    )
        if order.status == "cancelled" and kinds != ["reserve", "release"]:
            errors.append(
                f"#{number}: cancelled order inventory must end with release"
            )
        if order.status in _COMMITTED_ORDER_STATUSES and kinds != ["reserve", "commit"]:
            errors.append(
                f"#{number}: fulfilled or paid order inventory must end with commit"
            )
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
