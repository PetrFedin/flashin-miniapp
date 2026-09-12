from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ..delivery_models import DeliveryQuote
from ..models import DeliveryProvider, DeliveryShipment, Order
from ..provider_models import ProviderCommand
from .provider_commands import enqueue_provider_command

DELIVERY_PROVIDER_MODES = frozenset({"disabled", "manual", "sandbox", "live"})
DELIVERY_BOOKING_COMMAND = "delivery.shipment.book"
DELIVERY_COMMAND_PROVIDER = "delivery"


class DeliveryProviderConfigurationError(ValueError):
    pass


def _provider_config(provider: DeliveryProvider) -> dict[str, Any]:
    raw = str(provider.config_json or "{}").strip() or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DeliveryProviderConfigurationError(
            f"Delivery provider {provider.code} has invalid configuration"
        ) from exc
    if not isinstance(parsed, dict):
        raise DeliveryProviderConfigurationError(
            f"Delivery provider {provider.code} configuration must be an object"
        )
    return parsed


def configured_delivery_provider_mode(provider: DeliveryProvider) -> str:
    if not provider.active:
        return "disabled"
    config = _provider_config(provider)
    mode = str(config.get("mode") or "manual").strip().lower()
    if mode not in DELIVERY_PROVIDER_MODES:
        raise DeliveryProviderConfigurationError(
            f"Delivery provider {provider.code} has unsupported mode {mode!r}"
        )
    return mode


def resolve_delivery_provider_mode(
    db: Session,
    provider_code: str,
    *,
    lock: bool = False,
) -> str:
    code = str(provider_code or "").strip().lower()
    if code == "pickup":
        return "manual"
    query = db.query(DeliveryProvider).filter(DeliveryProvider.code == code)
    if lock:
        query = query.with_for_update()
    provider = query.first()
    if provider is None:
        return "disabled"
    return configured_delivery_provider_mode(provider)


def delivery_provider_accepts_quotes(db: Session, provider_code: str) -> bool:
    try:
        return resolve_delivery_provider_mode(db, provider_code) != "disabled"
    except DeliveryProviderConfigurationError:
        return False


def shipment_provider_mode(shipment: DeliveryShipment) -> str:
    try:
        payload = json.loads(str(shipment.raw_payload or "{}"))
    except json.JSONDecodeError as exc:
        raise DeliveryProviderConfigurationError(
            "Shipment provider snapshot is invalid"
        ) from exc
    if not isinstance(payload, dict):
        raise DeliveryProviderConfigurationError(
            "Shipment provider snapshot must be an object"
        )
    mode = str(payload.get("provider_mode") or "manual").strip().lower()
    if mode not in DELIVERY_PROVIDER_MODES:
        raise DeliveryProviderConfigurationError(
            f"Shipment has unsupported provider mode {mode!r}"
        )
    return mode


def enqueue_delivery_booking(
    db: Session,
    *,
    shipment: DeliveryShipment,
    order: Order,
    quote: DeliveryQuote,
    provider_mode: str,
) -> ProviderCommand | None:
    mode = str(provider_mode or "").strip().lower()
    if mode == "manual":
        return None
    if mode == "disabled":
        raise DeliveryProviderConfigurationError(
            "Delivery provider is disabled"
        )
    if mode not in {"sandbox", "live"}:
        raise DeliveryProviderConfigurationError(
            f"Unsupported delivery provider mode {mode!r}"
        )

    return enqueue_provider_command(
        db,
        provider=DELIVERY_COMMAND_PROVIDER,
        command_type=DELIVERY_BOOKING_COMMAND,
        idempotency_key=f"shipment-book:{shipment.id}:quote:{quote.id}",
        aggregate_type="delivery_shipment",
        aggregate_id=shipment.id,
        payload={
            "shipment_id": int(shipment.id),
            "order_id": int(order.id),
            "quote_id": int(quote.id),
            "quote_public_id": quote.public_id,
            "provider_code": quote.provider_code,
            "service_code": quote.service_code,
            "provider_mode": mode,
            "currency": quote.currency,
            "price": str(quote.price),
            "address_snapshot": quote.address_snapshot,
        },
    )


def delivery_booking_command(
    db: Session,
    shipment_id: int,
    *,
    lock: bool = False,
) -> ProviderCommand | None:
    query = db.query(ProviderCommand).filter(
        ProviderCommand.provider == DELIVERY_COMMAND_PROVIDER,
        ProviderCommand.command_type == DELIVERY_BOOKING_COMMAND,
        ProviderCommand.aggregate_type == "delivery_shipment",
        ProviderCommand.aggregate_id == str(int(shipment_id)),
    )
    if lock:
        query = query.with_for_update()
    return query.first()
