from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..catalog_demand_models import ProductDemandRequest
from ..catalog_models import ProductMerchandising
from ..database import get_db, utcnow_naive
from ..models import Customer, Product, ProductVariant
from ..security import get_current_admin, get_current_customer
from ..services.audit import log_admin_action
from ..services.rbac import require_permission

router = APIRouter(prefix="/catalog", tags=["catalog-demand"])

DemandType = Literal["preorder", "made_to_order"]
DemandStatus = Literal["requested", "contacted", "confirmed", "cancelled"]


class DemandRequestCreate(BaseModel):
    product_id: int = Field(gt=0)
    variant_id: int | None = Field(default=None, gt=0)
    request_type: DemandType
    quantity: int = Field(default=1, ge=1, le=10)
    requested_size: str = Field(default="", max_length=32)
    requested_color: str = Field(default="", max_length=64)
    notes: str = Field(default="", max_length=2000)


class DemandRequestAdminUpdate(BaseModel):
    status: DemandStatus
    admin_note: str = Field(default="", max_length=2000)


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def _active_key(customer_id: int, product_id: int, variant_id: int | None, request_type: str) -> str:
    return f"{customer_id}:{product_id}:{variant_id or 0}:{request_type}"


def _serialize(
    row: ProductDemandRequest,
    *,
    product: Product | None = None,
    admin: bool = False,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": int(row.id),
        "product_id": int(row.product_id),
        "variant_id": int(row.variant_id) if row.variant_id is not None else None,
        "request_type": str(row.request_type),
        "quantity": int(row.quantity),
        "requested_size": str(row.requested_size or ""),
        "requested_color": str(row.requested_color or ""),
        "notes": str(row.notes or ""),
        "status": str(row.status),
        "created_at": _utc_iso(row.created_at),
        "updated_at": _utc_iso(row.updated_at),
    }
    if product is not None:
        payload["product_title"] = str(product.title)
        payload["product_sku"] = str(product.sku)
    if admin:
        payload["customer_id"] = int(row.customer_id)
        payload["admin_note"] = str(row.admin_note or "")
    return payload


def _products_by_id(db: Session, rows: list[ProductDemandRequest]) -> dict[int, Product]:
    ids = sorted({int(row.product_id) for row in rows})
    if not ids:
        return {}
    return {row.id: row for row in db.query(Product).filter(Product.id.in_(ids)).all()}


def _eligible_product(
    db: Session,
    *,
    product_id: int,
    request_type: str,
) -> tuple[Product, ProductMerchandising]:
    product = (
        db.query(Product)
        .filter(Product.id == product_id, Product.active.is_(True))
        .first()
    )
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    merchandising = (
        db.query(ProductMerchandising)
        .filter(ProductMerchandising.product_id == product.id)
        .first()
    )
    configured = str(merchandising.availability_status) if merchandising else "in_stock"
    if configured not in {"preorder", "made_to_order"}:
        raise HTTPException(
            status_code=409,
            detail="Product is not configured for preorder or made-to-order demand",
        )
    if configured != request_type:
        raise HTTPException(
            status_code=409,
            detail=f"Request type must match configured availability: {configured}",
        )

    local_available_qty = sum(max(int(item.stock_qty) - int(item.reserved_qty), 0) for item in product.variants)
    if local_available_qty > 0:
        raise HTTPException(
            status_code=409,
            detail="Product has local stock; use the normal cart checkout flow",
        )
    return product, merchandising


@router.post("/demand-requests")
def create_demand_request(
    payload: DemandRequestCreate,
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    product, _ = _eligible_product(
        db,
        product_id=payload.product_id,
        request_type=payload.request_type,
    )

    variant: ProductVariant | None = None
    if payload.variant_id is not None:
        variant = (
            db.query(ProductVariant)
            .filter(
                ProductVariant.id == payload.variant_id,
                ProductVariant.product_id == product.id,
            )
            .first()
        )
        if not variant:
            raise HTTPException(status_code=400, detail="Variant does not belong to product")

    key = _active_key(customer.id, product.id, variant.id if variant else None, payload.request_type)
    existing = (
        db.query(ProductDemandRequest)
        .filter(ProductDemandRequest.active_request_key == key)
        .first()
    )
    if existing:
        return _serialize(existing, product=product)

    requested_size = (payload.requested_size or (variant.size if variant else "")).strip()
    requested_color = (payload.requested_color or (variant.color if variant else "")).strip()
    now = utcnow_naive()
    row = ProductDemandRequest(
        customer_id=customer.id,
        product_id=product.id,
        variant_id=variant.id if variant else None,
        request_type=payload.request_type,
        quantity=payload.quantity,
        requested_size=requested_size,
        requested_color=requested_color,
        notes=payload.notes.strip(),
        status="requested",
        admin_note="",
        active_request_key=key,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        concurrent = (
            db.query(ProductDemandRequest)
            .filter(ProductDemandRequest.active_request_key == key)
            .first()
        )
        if concurrent:
            return _serialize(concurrent, product=product)
        raise HTTPException(status_code=409, detail="Demand request already exists") from exc
    db.refresh(row)
    return _serialize(row, product=product)


@router.get("/demand-requests/me")
def my_demand_requests(
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(ProductDemandRequest)
        .filter(ProductDemandRequest.customer_id == customer.id)
        .order_by(ProductDemandRequest.created_at.desc(), ProductDemandRequest.id.desc())
        .limit(100)
        .all()
    )
    products = _products_by_id(db, rows)
    return [_serialize(row, product=products.get(row.product_id)) for row in rows]


@router.patch("/demand-requests/{request_id}/cancel")
def cancel_my_demand_request(
    request_id: int,
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    row = (
        db.query(ProductDemandRequest)
        .filter(
            ProductDemandRequest.id == request_id,
            ProductDemandRequest.customer_id == customer.id,
        )
        .with_for_update()
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Demand request not found")
    if row.status == "cancelled":
        product = db.query(Product).filter(Product.id == row.product_id).first()
        return _serialize(row, product=product)
    row.status = "cancelled"
    row.active_request_key = None
    row.updated_at = utcnow_naive()
    db.commit()
    db.refresh(row)
    product = db.query(Product).filter(Product.id == row.product_id).first()
    return _serialize(row, product=product)


@router.get("/admin/demand-requests")
def admin_demand_requests(
    status: DemandStatus | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "demand.read")
    query = db.query(ProductDemandRequest)
    if status is not None:
        query = query.filter(ProductDemandRequest.status == status)
    rows = (
        query.order_by(ProductDemandRequest.created_at.desc(), ProductDemandRequest.id.desc())
        .limit(limit)
        .all()
    )
    products = _products_by_id(db, rows)
    return [
        _serialize(row, product=products.get(row.product_id), admin=True)
        for row in rows
    ]


@router.patch("/admin/demand-requests/{request_id}")
def admin_update_demand_request(
    request_id: int,
    payload: DemandRequestAdminUpdate,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "demand.write")
    row = (
        db.query(ProductDemandRequest)
        .filter(ProductDemandRequest.id == request_id)
        .with_for_update()
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Demand request not found")

    current = str(row.status)
    allowed = {
        "requested": {"contacted", "cancelled"},
        "contacted": {"confirmed", "cancelled"},
        "confirmed": {"cancelled"},
        "cancelled": set(),
    }
    if payload.status != current and payload.status not in allowed.get(current, set()):
        raise HTTPException(
            status_code=409,
            detail=f"Invalid demand status transition: {current} -> {payload.status}",
        )

    row.status = payload.status
    row.admin_note = payload.admin_note.strip()
    if row.status == "cancelled":
        row.active_request_key = None
    row.updated_at = utcnow_naive()
    log_admin_action(
        db,
        admin,
        "catalog.demand_request.update",
        "product_demand_request",
        row.id,
        {"status": row.status},
    )
    db.commit()
    db.refresh(row)
    product = db.query(Product).filter(Product.id == row.product_id).first()
    return _serialize(row, product=product, admin=True)
