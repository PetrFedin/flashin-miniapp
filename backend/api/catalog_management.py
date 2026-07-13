from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..models import Product, ProductVariant
from ..security import get_current_admin
from ..services.audit import log_admin_action
from ..services.rbac import require_permission

router = APIRouter(prefix="/admin/catalog", tags=["admin-catalog"])


class ProductIdsIn(BaseModel):
    product_ids: list[int] = Field(min_length=1, max_length=500)
    reason: str = Field(default="", max_length=500)


class BulkPriceIn(ProductIdsIn):
    mode: Literal["set", "increase_percent", "decrease_percent"] = "set"
    value: float = Field(ge=0)
    set_old_price: bool = True


class BulkCategoryIn(ProductIdsIn):
    category: str = Field(min_length=1, max_length=120)


class BulkStockIn(BaseModel):
    variant_ids: list[int] = Field(min_length=1, max_length=1000)
    mode: Literal["set", "increase", "decrease"] = "set"
    value: int = Field(ge=0)
    reason: str = Field(default="bulk stock update", max_length=500)


def _products(db: Session, product_ids: list[int]) -> list[Product]:
    unique_ids = list(dict.fromkeys(product_ids))
    rows = db.query(Product).filter(Product.id.in_(unique_ids)).all()
    found = {row.id for row in rows}
    missing = [product_id for product_id in unique_ids if product_id not in found]
    if missing:
        raise HTTPException(status_code=404, detail={"message": "Products not found", "ids": missing})
    return rows


@router.post("/archive")
def archive_products(payload: ProductIdsIn, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.archive")
    rows = _products(db, payload.product_ids)
    changed = 0
    for product in rows:
        if product.active:
            product.active = False
            product.updated_at = datetime.utcnow()
            changed += 1
    log_admin_action(db, admin, "product.bulk_archive", "product", "bulk", {
        "product_ids": payload.product_ids,
        "reason": payload.reason,
        "changed": changed,
    })
    db.commit()
    return {"ok": True, "changed": changed, "product_ids": payload.product_ids}


@router.post("/restore")
def restore_products(payload: ProductIdsIn, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.archive")
    rows = _products(db, payload.product_ids)
    changed = 0
    for product in rows:
        if not product.active:
            product.active = True
            product.updated_at = datetime.utcnow()
            changed += 1
    log_admin_action(db, admin, "product.bulk_restore", "product", "bulk", {
        "product_ids": payload.product_ids,
        "reason": payload.reason,
        "changed": changed,
    })
    db.commit()
    return {"ok": True, "changed": changed, "product_ids": payload.product_ids}


@router.post("/prices")
def bulk_update_prices(payload: BulkPriceIn, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "prices.write")
    rows = _products(db, payload.product_ids)
    changes = []
    for product in rows:
        before = float(product.price)
        if payload.mode == "set":
            after = payload.value
        elif payload.mode == "increase_percent":
            after = before * (1 + payload.value / 100)
        else:
            after = before * (1 - payload.value / 100)
        after = round(max(after, 0), 2)
        if payload.set_old_price and before != after:
            product.old_price = before
        product.price = after
        product.updated_at = datetime.utcnow()
        changes.append({"id": product.id, "before": before, "after": after})
    log_admin_action(db, admin, "product.bulk_price", "product", "bulk", {
        "mode": payload.mode,
        "value": payload.value,
        "reason": payload.reason,
        "changes": changes,
    })
    db.commit()
    return {"ok": True, "changed": len(changes), "changes": changes}


@router.post("/category")
def bulk_update_category(payload: BulkCategoryIn, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.write")
    rows = _products(db, payload.product_ids)
    for product in rows:
        product.category = payload.category.strip()
        product.updated_at = datetime.utcnow()
    log_admin_action(db, admin, "product.bulk_category", "product", "bulk", {
        "product_ids": payload.product_ids,
        "category": payload.category,
        "reason": payload.reason,
    })
    db.commit()
    return {"ok": True, "changed": len(rows), "category": payload.category}


@router.post("/stock")
def bulk_update_stock(payload: BulkStockIn, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "inventory.write")
    unique_ids = list(dict.fromkeys(payload.variant_ids))
    rows = db.query(ProductVariant).filter(ProductVariant.id.in_(unique_ids)).all()
    found = {row.id for row in rows}
    missing = [variant_id for variant_id in unique_ids if variant_id not in found]
    if missing:
        raise HTTPException(status_code=404, detail={"message": "Variants not found", "ids": missing})
    changes = []
    for variant in rows:
        before = variant.stock_qty
        if payload.mode == "set":
            after = payload.value
        elif payload.mode == "increase":
            after = before + payload.value
        else:
            after = max(before - payload.value, 0)
        variant.stock_qty = after
        changes.append({"id": variant.id, "sku": variant.sku, "before": before, "after": after})
    log_admin_action(db, admin, "inventory.bulk_update", "variant", "bulk", {
        "mode": payload.mode,
        "value": payload.value,
        "reason": payload.reason,
        "changes": changes,
    })
    db.commit()
    return {"ok": True, "changed": len(changes), "changes": changes}


@router.get("/archived")
def archived_products(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.read")
    rows = (
        db.query(Product)
        .options(joinedload(Product.images), joinedload(Product.variants))
        .filter(Product.active.is_(False))
        .order_by(Product.updated_at.desc())
        .all()
    )
    return rows
