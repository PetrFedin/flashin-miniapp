import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import DeliveryProvider, DeliveryShipment, Order
from ..schemas import DeliveryProviderIn, DeliveryProviderOut, DeliveryShipmentOut
from ..security import get_current_admin
from ..services.audit import log_admin_action
from ..services.delivery_providers import ensure_ready_shipment, transition_shipment
from ..services.rbac import require_permission

router = APIRouter(prefix="/delivery-providers", tags=["delivery-providers"])


@router.get("", response_model=list[DeliveryProviderOut])
def providers(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "orders.read")
    return db.query(DeliveryProvider).order_by(DeliveryProvider.code).all()


@router.post("", response_model=DeliveryProviderOut)
def upsert_provider(
    payload: DeliveryProviderIn,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "orders.write")
    row = db.query(DeliveryProvider).filter(DeliveryProvider.code == payload.code).first()
    if not row:
        row = DeliveryProvider(code=payload.code)
        db.add(row)
    row.name = payload.name
    row.active = payload.active
    row.config_json = json.dumps(payload.config_json, ensure_ascii=False)
    db.commit()
    db.refresh(row)
    return row


@router.post("/orders/{order_id}/shipment", response_model=DeliveryShipmentOut)
def create_order_shipment(
    order_id: int,
    provider_code: str = "courier",
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "orders.write")
    try:
        order = (
            db.query(Order)
            .filter(Order.id == order_id)
            .with_for_update()
            .first()
        )
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        try:
            shipment, created = ensure_ready_shipment(db, order, provider_code)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if created:
            log_admin_action(
                db,
                admin,
                "delivery.shipment.create",
                "delivery_shipment",
                shipment.id,
                {
                    "order_id": order.id,
                    "provider_code": shipment.provider_code,
                },
            )
        db.commit()
        db.refresh(shipment)
        return shipment
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


@router.patch("/shipments/{shipment_id}", response_model=DeliveryShipmentOut)
def patch_shipment(
    shipment_id: int,
    tracking_number: str = "",
    status: str = "shipped",
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "orders.write")
    try:
        shipment = (
            db.query(DeliveryShipment)
            .filter(DeliveryShipment.id == shipment_id)
            .with_for_update()
            .first()
        )
        if not shipment:
            raise HTTPException(status_code=404, detail="Shipment not found")
        previous_status = shipment.status
        try:
            order = transition_shipment(db, shipment, tracking_number, status)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        log_admin_action(
            db,
            admin,
            "delivery.shipment.update",
            "delivery_shipment",
            shipment.id,
            {
                "order_id": order.id,
                "from_status": previous_status,
                "status": shipment.status,
                "tracking_number": shipment.tracking_number,
                "order_status": order.status,
                "delivery_status": order.delivery_status,
            },
        )
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
def shipments(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "orders.read")
    return db.query(DeliveryShipment).order_by(DeliveryShipment.created_at.desc()).limit(300).all()