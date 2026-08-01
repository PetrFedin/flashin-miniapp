from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import DeliveryZone


SUPPORTED_DELIVERY_TYPES = frozenset({"pickup", "courier"})


def calculate_delivery_price(db: Session, delivery_type: str, address: str = "") -> float:
    normalized_type = str(delivery_type or "").strip().lower()
    if normalized_type not in SUPPORTED_DELIVERY_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported delivery type")

    settings = get_settings()
    zone = (
        db.query(DeliveryZone)
        .filter(
            DeliveryZone.delivery_type == normalized_type,
            DeliveryZone.active.is_(True),
        )
        .first()
    )
    if zone:
        return zone.price
    if normalized_type == "courier":
        return settings.courier_delivery_price
    return settings.pickup_delivery_price
