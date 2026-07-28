from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import InventoryAdjustment, InventorySnapshot, ProductVariant


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


def reserve_variant(db: Session, variant_id: int, quantity: int) -> ProductVariant:
    _validate_positive_quantity(quantity)
    variant = _load_locked_variant(db, variant_id)
    available_qty = variant.stock_qty - variant.reserved_qty
    if available_qty < quantity:
        raise HTTPException(status_code=409, detail=f"Size {variant.size} is out of stock")
    variant.reserved_qty += quantity
    return variant


def release_variant(db: Session, variant_id: int, quantity: int) -> None:
    _validate_positive_quantity(quantity)
    variant = _load_locked_variant(db, variant_id)
    if variant.reserved_qty < quantity:
        raise HTTPException(status_code=409, detail="Reserved quantity mismatch")
    variant.reserved_qty -= quantity


def commit_reserved_to_sold(db: Session, variant_id: int, quantity: int) -> None:
    _validate_positive_quantity(quantity)
    variant = _load_locked_variant(db, variant_id)
    if variant.reserved_qty < quantity:
        raise HTTPException(status_code=409, detail="Reserved quantity mismatch")
    if variant.stock_qty < quantity:
        raise HTTPException(status_code=409, detail="Inventory would become negative")
    variant.reserved_qty -= quantity
    variant.stock_qty -= quantity


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
