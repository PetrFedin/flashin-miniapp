import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import DeliveryProvider
from .delivery_providers import normalize_provider_code

_MONEY_STEP = Decimal("0.01")
_ZONE_CODE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_MAX_DELIVERY_PRICE = Decimal("10000000.00")


def normalize_delivery_zone(value: str) -> str:
    zone = str(value or "default").strip().lower()
    if not _ZONE_CODE.fullmatch(zone):
        raise HTTPException(status_code=400, detail="Invalid delivery zone")
    return zone


def _delivery_money(value: object, source: str) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(_MONEY_STEP, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(status_code=500, detail=f"Invalid {source} delivery price")
    if not amount.is_finite() or amount < 0 or amount > _MAX_DELIVERY_PRICE:
        raise HTTPException(status_code=500, detail=f"Invalid {source} delivery price")
    return amount


def _provider_config(provider: DeliveryProvider | None) -> dict:
    if not provider:
        return {}
    try:
        parsed = json.loads(provider.config_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="Delivery provider configuration is invalid") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=500, detail="Delivery provider configuration is invalid")
    return parsed


def _configured_price(config: dict, zone: str) -> object | None:
    zones = config.get("zones", {})
    if zones is not None and not isinstance(zones, dict):
        raise HTTPException(status_code=500, detail="Delivery provider zones configuration is invalid")
    if isinstance(zones, dict) and zone in zones:
        return zones[zone]
    if "base_price" in config:
        return config["base_price"]
    return None


def calculate_delivery_price(
    db: Session,
    provider_code: str,
    zone: str = "default",
) -> tuple[str, str, Decimal]:
    code = normalize_provider_code(provider_code)
    normalized_zone = normalize_delivery_zone(zone)
    provider = db.query(DeliveryProvider).filter(DeliveryProvider.code == code).first()
    if provider and not provider.active:
        raise HTTPException(status_code=409, detail="Delivery provider is inactive")

    config = _provider_config(provider)
    configured = _configured_price(config, normalized_zone)
    if configured is not None:
        price = _delivery_money(configured, "configured")
        return code, normalized_zone, price

    settings = get_settings()
    defaults = {
        "courier": settings.courier_delivery_price,
        "cdek": settings.cdek_delivery_price,
        "boxberry": settings.boxberry_delivery_price,
        "pickup": settings.pickup_delivery_price,
    }
    if code in defaults:
        price = _delivery_money(defaults[code], "runtime")
        return code, normalized_zone, price
    if provider:
        price = _delivery_money(settings.default_delivery_price, "default")
        return code, normalized_zone, price
    raise HTTPException(status_code=404, detail="Delivery provider not found")
