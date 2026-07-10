from fastapi import APIRouter, Depends
from ..services.delivery_providers import calculate_delivery_price

router = APIRouter(prefix="/delivery-quotes", tags=["delivery-quotes"])


@router.get("")
def quote(provider_code: str = "courier", zone: str = "default"):
    return {"provider_code": provider_code, "zone": zone, "price": calculate_delivery_price(provider_code, zone)}
