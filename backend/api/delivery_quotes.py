from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..services.delivery_pricing import calculate_delivery_price

router = APIRouter(prefix="/delivery-quotes", tags=["delivery-quotes"])


@router.get("")
def quote(
    provider_code: str = Query(default="courier", min_length=1, max_length=64),
    zone: str = Query(default="default", min_length=1, max_length=64),
    db: Session = Depends(get_db),
):
    normalized_provider, normalized_zone, price = calculate_delivery_price(
        db,
        provider_code,
        zone,
    )
    return {
        "provider_code": normalized_provider,
        "zone": normalized_zone,
        "price": float(price),
        "currency": "RUB",
    }
