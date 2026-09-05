from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from ..catalog_intent_models import ProductIntentRequest
from ..catalog_models import ProductMerchandising
from ..database import get_db, utcnow_naive
from ..models import Customer, Product, ProductVariant
from ..security import get_current_admin, get_current_customer
from ..services.audit import log_admin_action
from ..services.rbac import require_permission

router = APIRouter(prefix="/catalog", tags=["catalog-intents"])

IntentType = Literal["preorder", "made_to_order"]
IntentStatus = Literal["requested", "working", "ready", "closed", "cancelled"]
_ACTIVE_STATUSES = {"requested", "working", "ready"}
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "requested": {"requested", "working", "cancelled"},
    "working": {"working", "ready", "cancelled"},
    "ready": {"ready", "closed", "cancelled"},
    "closed": {"closed"},
    "cancelled": {"cancelled"},
}


class ProductIntentCreate(BaseModel):
    product_id: int
    variant_id: int | None = None
    quantity: int = Field(default=1, ge=1, le=5)
    requested_size: str = Field(default="", max_length=32)
    requested_color: str = Field(default="", max_length=64)
    notes: str = Field(default="", max_length=2000)


class ProductIntentAdminUpdate(BaseModel):
    status: IntentStatus | None = None
    admin_note: str | None = Field(default=None, max_length=2000)
    quote_amount: float | None = Field(default=None, ge=0)
    quote_currency: str | None = Field(default=None, min_length=1, max_length=8)
    estimated_ready_at: datetime | None = None


def _utc_naive(value: datetime | None, field: str) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(status_code=400, detail=f"{field} must include a timezone")
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def _active_key(customer_id: int, product_id: int, variant_id: int | None) -> str:
    return f"{customer_id}:{product_id}:{variant_id or 0}"


def _intent_merchandising(db: Session, product_id: int) -> ProductMerchandising:
    merch = (
        db.query(ProductMerchandising)
        .filter(ProductMerchandising.product_id == product_id)
        .first()
    )
    if not merch or merch.availability_status not in {"preorder", "made_to_order"}:
        raise HTTPException(
            status_code=409,
            detail="Product is not configured for preorder or made-to-order requests",
        )
    return merch


def _inventory_state_valid(variant: ProductVariant) -> bool:
    return (
        variant.stock_qty >= 0
        and variant.reserved_qty >= 0
        and variant.reserved_qty <= variant.stock_qty
    )


def _intent_variant_eligible(variant: ProductVariant) -> bool:
    return _inventory_state_valid(variant) and variant.available_qty <= 0


def _normal_checkout_available(product: Product, variant_id: int | None) -> bool:
    if variant_id is not None:
        for variant in product.variants:
            if variant.id == variant_id:
                return _inventory_state_valid(variant) and variant.available_qty > 0
        return False
    return any(_inventory_state_valid(variant) and variant.available_qty > 0 for variant in product.variants)


def _serialize_intent(
    row: ProductIntentRequest,
    product: Product | None,
    variant: ProductVariant | None,
    *,
    admin: bool = False,
) -> dict[str, object]:
    result: dict[str, object] = {
        "id": row.id,
        "product_id": row.product_id,
        "product_title": product.title if product else "",
        "variant_id": row.variant_id,
        "variant_size": variant.size if variant else row.requested_size,
        "variant_color": variant.color if variant else row.requested_color,
        "intent_type": row.intent_type,
        "quantity": row.quantity,
        "requested_size": row.requested_size,
        "requested_color": row.requested_color,
        "notes": row.notes,
        "status": row.status,
        "quote_amount": row.quote_amount,
        "quote_currency": row.quote_currency,
        "estimated_ready_at": _iso_utc(row.estimated_ready_at),
        "created_at": _iso_utc(row.created_at),
        "updated_at": _iso_utc(row.updated_at),
        "payment_allowed": False,
        "normal_checkout_available": bool(product and _normal_checkout_available(product, row.variant_id)),
    }
    if admin:
        result["customer_id"] = row.customer_id
        result["admin_note"] = row.admin_note
    return result


def _load_products_and_variants(
    db: Session,
    rows: list[ProductIntentRequest],
) -> tuple[dict[int, Product], dict[int, ProductVariant]]:
    product_ids = sorted({row.product_id for row in rows})
    variant_ids = sorted({row.variant_id for row in rows if row.variant_id is not None})
    products = {
        product.id: product
        for product in (
            db.query(Product)
            .options(joinedload(Product.variants))
            .filter(Product.id.in_(product_ids))
            .all()
            if product_ids
            else []
        )
    }
    variants = {
        variant.id: variant
        for variant in (
            db.query(ProductVariant).filter(ProductVariant.id.in_(variant_ids)).all()
            if variant_ids
            else []
        )
    }
    return products, variants


@router.get("/intents/eligible-products")
def eligible_intent_products(db: Session = Depends(get_db)):
    merch_rows = (
        db.query(ProductMerchandising)
        .filter(ProductMerchandising.availability_status.in_(["preorder", "made_to_order"]))
        .order_by(ProductMerchandising.grid_rank.asc(), ProductMerchandising.product_id.asc())
        .limit(300)
        .all()
    )
    product_ids = [row.product_id for row in merch_rows]
    if not product_ids:
        return []
    products = (
        db.query(Product)
        .options(joinedload(Product.images), joinedload(Product.variants))
        .filter(Product.id.in_(product_ids), Product.active.is_(True))
        .all()
    )
    by_id = {product.id: product for product in products}
    result: list[dict[str, object]] = []
    for merch in merch_rows:
        product = by_id.get(merch.product_id)
        if not product:
            continue
        images = sorted(product.images, key=lambda image: (image.sort_order, image.id))
        variants = sorted(product.variants, key=lambda item: (item.color or "", item.size or "", item.id))
        eligible_variant_ids = {
            variant.id for variant in variants if _intent_variant_eligible(variant)
        }
        if variants and not eligible_variant_ids:
            continue
        result.append(
            {
                "id": product.id,
                "title": product.title,
                "brand": product.brand,
                "price": product.price,
                "currency": product.currency,
                "intent_type": merch.availability_status,
                "image_url": images[0].url if images else "",
                "variants": [
                    {
                        "id": variant.id,
                        "size": variant.size,
                        "color": variant.color,
                        "available_qty": variant.available_qty,
                        "intent_eligible": variant.id in eligible_variant_ids,
                    }
                    for variant in variants
                ],
            }
        )
    return result


@router.post("/intents")
def create_product_intent(
    payload: ProductIntentCreate,
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    product = (
        db.query(Product)
        .options(joinedload(Product.variants))
        .filter(Product.id == payload.product_id, Product.active.is_(True))
        .first()
    )
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    merch = _intent_merchandising(db, product.id)

    variant: ProductVariant | None = None
    if payload.variant_id is not None:
        variant = (
            db.query(ProductVariant)
            .filter(
                ProductVariant.id == payload.variant_id,
                ProductVariant.product_id == product.id,
            )
            .with_for_update()
            .first()
        )
        if not variant:
            raise HTTPException(status_code=404, detail="Variant not found for this product")
        if not _inventory_state_valid(variant):
            raise HTTPException(status_code=409, detail="Inventory state is invalid")
        if variant.available_qty > 0:
            raise HTTPException(status_code=409, detail="Selected variant is available for normal cart checkout")
    else:
        if any(_inventory_state_valid(item) and item.available_qty > 0 for item in product.variants):
            raise HTTPException(
                status_code=409,
                detail="Select an unavailable variant or use normal cart checkout for available stock",
            )

    requested_size = payload.requested_size.strip()
    requested_color = payload.requested_color.strip()
    if variant:
        requested_size = requested_size or variant.size
        requested_color = requested_color or variant.color

    now = utcnow_naive()
    row = ProductIntentRequest(
        customer_id=customer.id,
        product_id=product.id,
        variant_id=variant.id if variant else None,
        intent_type=merch.availability_status,
        quantity=payload.quantity,
        requested_size=requested_size,
        requested_color=requested_color,
        notes=payload.notes.strip(),
        status="requested",
        quote_currency=(product.currency or "RUB").upper()[:8],
        active_request_key=_active_key(customer.id, product.id, variant.id if variant else None),
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="An active request already exists for this product variant",
        ) from exc
    db.refresh(row)
    return _serialize_intent(row, product, variant)


@router.get("/intents/me")
def my_product_intents(
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(ProductIntentRequest)
        .filter(ProductIntentRequest.customer_id == customer.id)
        .order_by(ProductIntentRequest.created_at.desc(), ProductIntentRequest.id.desc())
        .limit(100)
        .all()
    )
    products, variants = _load_products_and_variants(db, rows)
    return [
        _serialize_intent(row, products.get(row.product_id), variants.get(row.variant_id) if row.variant_id else None)
        for row in rows
    ]


@router.get("/admin/intents")
def admin_product_intents(
    status: IntentStatus | None = None,
    intent_type: IntentType | None = None,
    product_id: int | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "products.read")
    query = db.query(ProductIntentRequest)
    if status:
        query = query.filter(ProductIntentRequest.status == status)
    if intent_type:
        query = query.filter(ProductIntentRequest.intent_type == intent_type)
    if product_id is not None:
        query = query.filter(ProductIntentRequest.product_id == product_id)
    rows = query.order_by(ProductIntentRequest.created_at.desc(), ProductIntentRequest.id.desc()).limit(limit).all()
    products, variants = _load_products_and_variants(db, rows)
    return [
        _serialize_intent(
            row,
            products.get(row.product_id),
            variants.get(row.variant_id) if row.variant_id else None,
            admin=True,
        )
        for row in rows
    ]


@router.patch("/admin/intents/{intent_id}")
def update_admin_product_intent(
    intent_id: int,
    payload: ProductIntentAdminUpdate,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "products.write")
    row = (
        db.query(ProductIntentRequest)
        .filter(ProductIntentRequest.id == intent_id)
        .with_for_update()
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Product intent request not found")

    changes: dict[str, object] = {}
    fields_set = payload.model_fields_set
    if payload.status is not None:
        if payload.status not in _ALLOWED_TRANSITIONS.get(row.status, {row.status}):
            raise HTTPException(
                status_code=409,
                detail=f"Invalid intent status transition: {row.status} -> {payload.status}",
            )
        if payload.status != row.status:
            changes["status"] = {"from": row.status, "to": payload.status}
            row.status = payload.status
        row.active_request_key = (
            _active_key(row.customer_id, row.product_id, row.variant_id)
            if row.status in _ACTIVE_STATUSES
            else None
        )
    if "admin_note" in fields_set:
        row.admin_note = (payload.admin_note or "").strip()
        changes["admin_note_changed"] = True
    if "quote_amount" in fields_set:
        if payload.quote_amount is None:
            row.quote_amount = None
        else:
            if not math.isfinite(payload.quote_amount):
                raise HTTPException(status_code=400, detail="quote_amount must be finite")
            row.quote_amount = round(float(payload.quote_amount), 2)
        changes["quote_amount"] = row.quote_amount
    if "quote_currency" in fields_set:
        if payload.quote_currency is None:
            raise HTTPException(status_code=400, detail="quote_currency cannot be null")
        currency = payload.quote_currency.strip().upper()
        if not currency:
            raise HTTPException(status_code=400, detail="quote_currency is required")
        row.quote_currency = currency
        changes["quote_currency"] = currency
    if "estimated_ready_at" in fields_set:
        row.estimated_ready_at = _utc_naive(payload.estimated_ready_at, "estimated_ready_at")
        changes["estimated_ready_at"] = _iso_utc(row.estimated_ready_at)

    row.updated_at = utcnow_naive()
    log_admin_action(
        db,
        admin,
        "catalog.intent.update",
        entity_type="product_intent_request",
        entity_id=row.id,
        payload=changes,
    )
    db.commit()
    db.refresh(row)
    product = (
        db.query(Product)
        .options(joinedload(Product.variants))
        .filter(Product.id == row.product_id)
        .first()
    )
    variant = db.query(ProductVariant).filter(ProductVariant.id == row.variant_id).first() if row.variant_id else None
    return _serialize_intent(row, product, variant, admin=True)
