from sqlalchemy.orm import Session
from ..config import get_settings
from ..models import DeliveryZone


def calculate_delivery_price(db: Session, delivery_type: str, address: str = "") -> float:
    settings = get_settings()
    zone = db.query(DeliveryZone).filter(DeliveryZone.delivery_type == delivery_type, DeliveryZone.active == True).first()
    if zone:
        return zone.price
    if delivery_type == "courier":
        return settings.courier_delivery_price
    if delivery_type == "pickup":
        return settings.pickup_delivery_price
    return settings.default_delivery_price
