import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import DeliveryProvider, DeliveryShipment
from ..schemas import DeliveryProviderIn, DeliveryProviderOut, DeliveryShipmentOut
from ..security import get_current_admin
from ..services.delivery_providers import (
    create_shipment,
    normalize_provider_code,
    update_tracking,
)
from ..services.rbac import require_permission

router = APIRouter(prefix="/delivery-providers", tags=["delivery-providers"])
_MAX_PROVIDER_CONFIG_BYTES = 64 * 1024


def _provider_name(value: str) -> str:
    name = str(value or "").strip()
    if len(name) < 2:
        raise HTTPException(status_code=400, detail="Delivery provider name is too short")
    if len(name) > 255:
        raise HTTPException(status_code=400, detail="Delivery provider name is too long")
    return name


def _provider_config(value: dict) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid delivery provider configuration") from exc
    if len(encoded.encode("utf-8")) > _MAX_PROVIDER_CONFIG_BYTES:
        raise HTTPException(status_code=413, detail="Delivery provider configuration is too large")
    return encoded


@router.get("", response_model=list[DeliveryProviderOut])
def providers(
    active: bool | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "orders.read")
    query = db.query(DeliveryProvider)
    if active is not None:
        query = query.filter(DeliveryProvider.active.is_(active))
    return query.order_by(DeliveryProvider.code.asc()).limit(limit).all()


@router.post("", response_model=DeliveryProviderOut)
def upsert_provider(
    payload: DeliveryProviderIn,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "orders.write")
    code = normalize_provider_code(payload.code)
    name = _provider_name(payload.name)
    config_json = _provider_config(payload.config_json)
    try:
        row = (
            db.query(DeliveryProvider)
            .filter(DeliveryProvider.code == code)
            .with_for_update()
            .first()
        )
        if not row:
            row = DeliveryProvider(code=code)
            db.add(row)
        row.name = name
        row.active = payload.active
        row.config_json = config_json
        db.commit()
        db.refresh(row)
        return row
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Delivery provider already exists") from exc
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


@router.post("/orders/{order_id}/shipment", response_model=DeliveryShipmentOut)
def create_order_shipment(
    order_id: int,
    provider_code: str = Query(default="courier", min_length=1, max_length=64),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "orders.write")
    try:
        shipment = create_shipment(db, order_id, provider_code)
        db.commit()
        db.refresh(shipment)
        return shipment
    except IntegrityError:
        db.rollback()
        existing = db.query(DeliveryShipment).filter(DeliveryShipment.order_id == order_id).first()
        if existing:
            return existing
        raise HTTPException(status_code=409, detail="Shipment already exists")
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


@router.patch("/shipments/{shipment_id}", response_model=DeliveryShipmentOut)
def patch_shipment(
    shipment_id: int,
    tracking_number: str = Query(default="", max_length=255),
    status: str = Query(default="shipped", min_length=1, max_length=64),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "orders.write")
    try:
        shipment = update_tracking(db, shipment_id, tracking_number, status)
        db.commit()
        db.refresh(shipment)
        return shipment
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


@router.get("/shipments", response_model=list[DeliveryShipmentOut])
def shipments(
    status: str | None = Query(default=None, max_length=64),
    provider_code: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=300, ge=1, le=500),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "orders.read")
    query = db.query(DeliveryShipment)
    normalized_status = str(status or "").strip().lower()
    if normalized_status:
        if normalized_status not in {
            "created",
            "shipped",
            "delivery_failed",
            "delivered",
            "returned",
            "cancelled",
        }:
            raise HTTPException(status_code=400, detail="Unsupported shipment status")
        query = query.filter(DeliveryShipment.status == normalized_status)
    normalized_provider = str(provider_code or "").strip()
    if normalized_provider:
        normalized_provider = normalize_provider_code(normalized_provider)
        query = query.filter(DeliveryShipment.provider_code == normalized_provider)
    return (
        query.order_by(DeliveryShipment.created_at.desc(), DeliveryShipment.id.desc())
        .limit(limit)
        .all()
    )
