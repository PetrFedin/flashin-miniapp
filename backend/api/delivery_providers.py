import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import DeliveryProvider, DeliveryShipment, Order
from ..schemas import DeliveryProviderIn, DeliveryProviderOut, DeliveryShipmentOut
from ..security import get_current_admin
from ..services.delivery_providers import create_shipment, update_tracking
from ..services.rbac import require_permission

router = APIRouter(prefix="/delivery-providers", tags=["delivery-providers"])


@router.get("", response_model=list[DeliveryProviderOut])
def providers(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "delivery.read")
    return db.query(DeliveryProvider).order_by(DeliveryProvider.code).all()


@router.post("", response_model=DeliveryProviderOut)
def upsert_provider(payload: DeliveryProviderIn, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "delivery.write")
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
def create_order_shipment(order_id: int, provider_code: str = "courier", admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "delivery.write")
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    shipment = create_shipment(db, order, provider_code)
    db.commit()
    db.refresh(shipment)
    return shipment


@router.patch("/shipments/{shipment_id}", response_model=DeliveryShipmentOut)
def patch_shipment(shipment_id: int, tracking_number: str = "", status: str = "shipped", admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "delivery.write")
    shipment = db.query(DeliveryShipment).filter(DeliveryShipment.id == shipment_id).first()
    if not shipment:
        raise HTTPException(status_code=404, detail="Shipment not found")
    update_tracking(shipment, tracking_number, status)
    db.commit()
    return shipment


@router.get("/shipments", response_model=list[DeliveryShipmentOut])
def shipments(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "delivery.read")
    return db.query(DeliveryShipment).order_by(DeliveryShipment.created_at.desc()).limit(300).all()
