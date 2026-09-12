from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..catalog_models import ProductMerchandising
from ..database import get_db, utcnow_naive
from ..models import Product
from ..security import get_current_admin
from ..services.audit import log_admin_action
from ..services.pricing import (
    load_product_price_quotes,
    normalize_utc_naive,
    quote_product_price,
    utc_iso,
)
from ..services.rbac import require_permission

router = APIRouter(prefix="/catalog", tags=["catalog-pricing"])


class AdminProductPricingIn(BaseModel):
    promo_price: float | None = Field(default=None, gt=0)
    sale_starts_at: datetime | None = None
    sale_ends_at: datetime | None = None

    model_config = ConfigDict(extra="forbid")


def _load_product(db: Session, product_id: int, *, lock: bool = False, active_only: bool = False) -> Product:
    query = db.query(Product).filter(Product.id == product_id)
    if active_only:
        query = query.filter(Product.active.is_(True))
    if lock:
        query = query.with_for_update()
    product = query.first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product


def _admin_queue_row(product: Product, merchandising: ProductMerchandising | None) -> dict[str, object]:
    base = {
        "product_id": product.id,
        "sku": product.sku,
        "title": product.title,
        "active": product.active,
        "regular_price": product.price,
        "configured_promo_price": merchandising.promo_price if merchandising else None,
        "sale_starts_at": utc_iso(merchandising.sale_starts_at if merchandising else None),
        "sale_ends_at": utc_iso(merchandising.sale_ends_at if merchandising else None),
        "configuration_error": None,
    }
    try:
        base.update(quote_product_price(product, merchandising).admin_payload())
    except HTTPException as exc:
        base.update(
            {
                "effective_price": None,
                "compare_at_price": None,
                "promo_active": False,
                "configuration_error": str(exc.detail),
            }
        )
    return base


@router.get("/pricing")
def public_catalog_pricing(
    product_id: list[int] | None = Query(default=None),
    db: Session = Depends(get_db),
):
    product_ids = list(dict.fromkeys(int(value) for value in (product_id or []) if int(value) > 0))
    if not product_ids:
        return []
    if len(product_ids) > 100:
        raise HTTPException(status_code=400, detail="At most 100 product prices can be requested")
    products = (
        db.query(Product)
        .filter(Product.id.in_(product_ids), Product.active.is_(True))
        .order_by(Product.id.asc())
        .all()
    )
    quotes = load_product_price_quotes(db, products)
    return [quotes[product.id].public_payload() for product in products]


@router.get("/products/{product_id}/pricing")
def public_product_pricing(product_id: int, db: Session = Depends(get_db)):
    product = _load_product(db, product_id, active_only=True)
    return load_product_price_quotes(db, [product])[product.id].public_payload()


@router.get("/admin/pricing")
def admin_pricing_queue(
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "products.read")
    products = db.query(Product).order_by(Product.active.desc(), Product.id.asc()).limit(1000).all()
    product_ids = [product.id for product in products]
    merchandising = {
        row.product_id: row
        for row in db.query(ProductMerchandising).filter(ProductMerchandising.product_id.in_(product_ids)).all()
    } if product_ids else {}
    return [_admin_queue_row(product, merchandising.get(product.id)) for product in products]


@router.get("/admin/products/{product_id}/pricing")
def admin_product_pricing(
    product_id: int,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "products.read")
    product = _load_product(db, product_id)
    merchandising = (
        db.query(ProductMerchandising)
        .filter(ProductMerchandising.product_id == product.id)
        .first()
    )
    return _admin_queue_row(product, merchandising)


@router.patch("/admin/products/{product_id}/pricing")
def admin_update_product_pricing(
    product_id: int,
    payload: AdminProductPricingIn,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "products.write")
    fields = set(payload.model_fields_set)
    if not fields:
        raise HTTPException(status_code=400, detail="No pricing fields supplied")

    try:
        product = _load_product(db, product_id, lock=True)
        merchandising = (
            db.query(ProductMerchandising)
            .filter(ProductMerchandising.product_id == product.id)
            .with_for_update()
            .first()
        )
        if not merchandising:
            merchandising = ProductMerchandising(product_id=product.id)
            db.add(merchandising)
            db.flush()

        if "promo_price" in fields:
            merchandising.promo_price = payload.promo_price
        if "sale_starts_at" in fields:
            merchandising.sale_starts_at = normalize_utc_naive(payload.sale_starts_at)
        if "sale_ends_at" in fields:
            merchandising.sale_ends_at = normalize_utc_naive(payload.sale_ends_at)
        merchandising.updated_at = utcnow_naive()

        quote = quote_product_price(product, merchandising)
        log_admin_action(
            db,
            admin,
            "catalog.pricing.update",
            "product",
            product.id,
            {
                "fields": sorted(fields),
                "promo_active": quote.promo_active,
            },
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Pricing update conflicts with current product state") from exc
    except Exception:
        db.rollback()
        raise

    product = _load_product(db, product_id)
    merchandising = (
        db.query(ProductMerchandising)
        .filter(ProductMerchandising.product_id == product.id)
        .first()
    )
    return _admin_queue_row(product, merchandising)
