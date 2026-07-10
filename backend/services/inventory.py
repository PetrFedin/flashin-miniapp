from fastapi import HTTPException
from sqlalchemy.orm import Session
from ..models import ProductVariant


def reserve_variant(db: Session, variant_id: int, quantity: int) -> ProductVariant:
    variant = db.query(ProductVariant).filter(ProductVariant.id == variant_id).with_for_update().first()
    if not variant:
        raise HTTPException(status_code=404, detail=f"Variant {variant_id} not found")
    if variant.available_qty < quantity:
        raise HTTPException(status_code=409, detail=f"Size {variant.size} is out of stock")
    variant.reserved_qty += quantity
    return variant


def release_variant(db: Session, variant_id: int, quantity: int) -> None:
    variant = db.query(ProductVariant).filter(ProductVariant.id == variant_id).with_for_update().first()
    if variant:
        variant.reserved_qty = max(variant.reserved_qty - quantity, 0)


def commit_reserved_to_sold(db: Session, variant_id: int, quantity: int) -> None:
    variant = db.query(ProductVariant).filter(ProductVariant.id == variant_id).with_for_update().first()
    if not variant:
        raise HTTPException(status_code=404, detail=f"Variant {variant_id} not found")
    if variant.reserved_qty < quantity:
        raise HTTPException(status_code=409, detail="Reserved quantity mismatch")
    variant.reserved_qty -= quantity
    variant.stock_qty -= quantity
    if variant.stock_qty < 0:
        raise HTTPException(status_code=409, detail="Inventory would become negative")



from ..models import InventoryAdjustment, InventorySnapshot


def adjust_stock(db: Session, variant_id: int, new_stock_qty: int, reason: str = "", admin_id: int | None = None) -> ProductVariant:
    variant = db.query(ProductVariant).filter(ProductVariant.id == variant_id).with_for_update().first()
    if not variant:
        raise HTTPException(status_code=404, detail=f"Variant {variant_id} not found")
    if new_stock_qty < variant.reserved_qty:
        raise HTTPException(status_code=409, detail="Stock cannot be lower than reserved quantity")
    old = variant.stock_qty
    variant.stock_qty = new_stock_qty
    db.add(InventoryAdjustment(
        variant_id=variant.id,
        old_stock_qty=old,
        new_stock_qty=new_stock_qty,
        reason=reason,
        admin_id=admin_id,
    ))
    return variant


def snapshot_inventory(db: Session, source: str = "system") -> int:
    variants = db.query(ProductVariant).all()
    for variant in variants:
        db.add(InventorySnapshot(
            variant_id=variant.id,
            stock_qty=variant.stock_qty,
            reserved_qty=variant.reserved_qty,
            source=source,
        ))
    return len(variants)
