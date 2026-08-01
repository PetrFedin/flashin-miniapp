import json

from sqlalchemy.orm import Session

from ..database import utcnow_naive
from ..models import DeliveryShipment, Order


def calculate_delivery_price(provider_code: str, zone: str = "default") -> float:
    base = {
        "courier": 500,
        "cdek": 700,
        "boxberry": 650,
        "pickup": 0,
    }
    return float(base.get(provider_code, 500))


def create_shipment(db: Session, order: Order, provider_code: str = "courier") -> DeliveryShipment:
    price = calculate_delivery_price(provider_code)
    shipment = DeliveryShipment(
        order_id=order.id,
        provider_code=provider_code,
        tracking_number="",
        status="created",
        price=price,
        raw_payload=json.dumps({"provider": provider_code}, ensure_ascii=False),
    )
    db.add(shipment)
    return shipment


def update_tracking(shipment: DeliveryShipment, tracking_number: str, status: str = "shipped") -> None:
    shipment.tracking_number = tracking_number
    shipment.status = status
    shipment.updated_at = utcnow_naive()
