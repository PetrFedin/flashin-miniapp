from collections.abc import Mapping

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import (
    InventoryAdjustment,
    InventoryMovement,
    InventorySnapshot,
    ProductVariant,
)


def _validate_positive_quantity(quantity: int) -> None:
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be a positive integer")


def _validate_stock_quantity(quantity: int) -> None:
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
        raise HTTPException(status_code=400, detail="Stock quantity must be a non-negative integer")


def _validate_variant_state(variant: ProductVariant) -> None:
    if variant.stock_qty < 0 or variant.reserved_qty < 0:
        raise HTTPException(status_code=409, detail="Inventory quantities cannot be negative")
    if variant.reserved_qty > variant.stock_qty:
        raise HTTPException(status_code=409, detail="Reserved quantity exceeds stock")


def _normalize_quantities(quantities: Mapping[int, int]) -> dict[int, int]:
    normalized: dict[int, int] = {}
    for variant_id, quantity in quantities.items():
        if isinstance(variant_id, bool) or not isinstance(variant_id, int) or variant_id <= 0:
            raise HTTPException(status_code=400, detail="Variant id must be a positive integer")
        _validate_positive_quantity(quantity)
        normalized[variant_id] = normalized.get(variant_id, 0) + quantity
    if not normalized:
        raise HTTPException(status_code=400, detail="At least one inventory quantity is required")
    return normalized


def _load_locked_variant(db: Session, variant_id: int) -> ProductVariant:
    variant = (
        db.query(ProductVariant)
        .filter(ProductVariant.id == variant_id)
        .with_for_update()
        .first()
    )
    if not variant:
        raise HTTPException(status_code=404, detail=f"Variant {variant_id} not found")
    _validate_variant_state(variant)
    return variant


def _load_locked_variants(
    db: Session,
    quantities: Mapping[int, int],
) -> tuple[dict[int, int], dict[int, ProductVariant]]:
    normalized = _normalize_quantities(quantities)
    variants = (
        db.query(ProductVariant)
        .filter(ProductVariant.id.in_(sorted(normalized)))
        .order_by(ProductVariant.id.asc())
        .with_for_update()
        .all()
    )
    by_id = {variant.id: variant for variant in variants}
    missing = sorted(set(normalized) - set(by_id))
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Inventory variants not found: {', '.join(map(str, missing))}",
        )
    for variant in variants:
        _validate_variant_state(variant)
    return normalized, by_id


def _record_movement(
    db: Session,
    *,
    order_id: int | None,
    variant: ProductVariant,
    kind: str,
    quantity: int,
    stock_before: int,
    reserved_before: int,
    source: str,
) -> None:
    if order_id is None:
        return
    if isinstance(order_id, bool) or not isinstance(order_id, int) or order_id <= 0:
        raise HTTPException(status_code=400, detail="Order id must be a positive integer")
    normalized_source = (source or "").strip()[:128]
    if not normalized_source:
        raise HTTPException(status_code=400, detail="Inventory movement source is required")
    db.add(
        InventoryMovement(
            order_id=order_id,
            variant_id=variant.id,
            kind=kind,
            quantity=quantity,
            stock_before=stock_before,
            stock_after=variant.stock_qty,
            reserved_before=reserved_before,
            reserved_after=variant.reserved_qty,
            source=normalized_source,
        )
    )


def reserve_variant(
    db: Session,
    variant_id: int,
    quantity: int,
    *,
    order_id: int | None = None,
    source: str = "reserve",
) -> ProductVariant:
    _validate_positive_quantity(quantity)
    variant = _load_locked_variant(db, variant_id)
    available_qty = variant.stock_qty - variant.reserved_qty
    if available_qty < quantity:
        raise HTTPException(status_code=409, detail=f"Size {variant.size} is out of stock")
    stock_before = variant.stock_qty
    reserved_before = variant.reserved_qty
    variant.reserved_qty += quantity
    _record_movement(
        db,
        order_id=order_id,
        variant=variant,
        kind="reserve",
        quantity=quantity,
        stock_before=stock_before,
        reserved_before=reserved_before,
        source=source,
    )
    return variant


def release_variants(
    db: Session,
    quantities: Mapping[int, int],
    *,
    order_id: int | None = None,
    source: str = "release",
) -> None:
    normalized, variants = _load_locked_variants(db, quantities)
    for variant_id, quantity in normalized.items():
        if variants[variant_id].reserved_qty < quantity:
            raise HTTPException(
                status_code=409,
                detail=f"Reserved quantity mismatch for variant {variant_id}",
            )
    for variant_id, quantity in normalized.items():
        variant = variants[variant_id]
        stock_before = variant.stock_qty
        reserved_before = variant.reserved_qty
        variant.reserved_qty -= quantity
        _record_movement(
            db,
            order_id=order_id,
            variant=variant,
            kind="release",
            quantity=quantity,
            stock_before=stock_before,
            reserved_before=reserved_before,
            source=source,
        )


def release_variant(
    db: Session,
    variant_id: int,
    quantity: int,
    *,
    order_id: int | None = None,
    source: str = "release",
) -> None:
    release_variants(
        db,
        {variant_id: quantity},
        order_id=order_id,
        source=source,
    )


def commit_reservations_to_sold(
    db: Session,
    quantities: Mapping[int, int],
    *,
    order_id: int | None = None,
    source: str = "commit",
) -> None:
    normalized, variants = _load_locked_variants(db, quantities)
    for variant_id, quantity in normalized.items():
        variant = variants[variant_id]
        if variant.reserved_qty < quantity:
            raise HTTPException(
                status_code=409,
                detail=f"Reserved quantity mismatch for variant {variant_id}",
            )
        if variant.stock_qty < quantity:
            raise HTTPException(
                status_code=409,
                detail=f"Inventory would become negative for variant {variant_id}",
            )
    for variant_id, quantity in normalized.items():
        variant = variants[variant_id]
        stock_before = variant.stock_qty
        reserved_before = variant.reserved_qty
        variant.reserved_qty -= quantity
        variant.stock_qty -= quantity
        _record_movement(
            db,
            order_id=order_id,
            variant=variant,
            kind="commit",
            quantity=quantity,
            stock_before=stock_before,
            reserved_before=reserved_before,
            source=source,
        )


def commit_reserved_to_sold(
    db: Session,
    variant_id: int,
    quantity: int,
    *,
    order_id: int | None = None,
    source: str = "commit",
) -> None:
    commit_reservations_to_sold(
        db,
        {variant_id: quantity},
        order_id=order_id,
        source=source,
    )


def restore_sold_variants(
    db: Session,
    quantities: Mapping[int, int],
    *,
    order_id: int,
    source: str = "refund_return",
) -> bool:
    """Restore sold inventory once after a completed full refund.

    A single order can only have one full cumulative refund. The movement
    ledger's order/variant/kind uniqueness makes the stock restoration durable
    and idempotent across duplicate provider webhooks and reconciliation runs.
    """
    normalized, variants = _load_locked_variants(db, quantities)
    existing_rows = (
        db.query(InventoryMovement)
        .filter(
            InventoryMovement.order_id == order_id,
            InventoryMovement.kind == "return",
            InventoryMovement.variant_id.in_(sorted(normalized)),
        )
        .order_by(InventoryMovement.variant_id.asc())
        .with_for_update()
        .all()
    )
    if existing_rows:
        existing = {row.variant_id: row.quantity for row in existing_rows}
        if existing == normalized:
            return False
        raise HTTPException(
            status_code=409,
            detail="Refund inventory restoration is only partially recorded and requires review",
        )

    for variant_id, quantity in normalized.items():
        variant = variants[variant_id]
        stock_before = variant.stock_qty
        reserved_before = variant.reserved_qty
        variant.stock_qty += quantity
        _record_movement(
            db,
            order_id=order_id,
            variant=variant,
            kind="return",
            quantity=quantity,
            stock_before=stock_before,
            reserved_before=reserved_before,
            source=source,
        )
    return True


def adjust_stock(
    db: Session,
    variant_id: int,
    new_stock_qty: int,
    reason: str = "",
    admin_id: int | None = None,
) -> ProductVariant:
    _validate_stock_quantity(new_stock_qty)
    variant = _load_locked_variant(db, variant_id)
    if new_stock_qty < variant.reserved_qty:
        raise HTTPException(status_code=409, detail="Stock cannot be lower than reserved quantity")

    old_stock_qty = variant.stock_qty
    variant.stock_qty = new_stock_qty
    db.add(
        InventoryAdjustment(
            variant_id=variant.id,
            old_stock_qty=old_stock_qty,
            new_stock_qty=new_stock_qty,
            reason=(reason or "").strip()[:255],
            admin_id=admin_id,
        )
    )
    return variant


def snapshot_inventory(db: Session, source: str = "system") -> int:
    variants = db.query(ProductVariant).all()
    for variant in variants:
        _validate_variant_state(variant)
        db.add(
            InventorySnapshot(
                variant_id=variant.id,
                stock_qty=variant.stock_qty,
                reserved_qty=variant.reserved_qty,
                source=(source or "system").strip()[:64] or "system",
            )
        )
    return len(variants)
