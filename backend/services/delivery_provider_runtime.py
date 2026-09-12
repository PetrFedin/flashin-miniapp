from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from ..database import utcnow_naive
from ..delivery_models import DeliveryQuote
from ..models import DeliveryProvider, DeliveryShipment, Order
from ..provider_models import ProviderCommand
from .provider_commands import enqueue_provider_command

DELIVERY_PROVIDER_MODES = frozenset({"disabled", "manual", "sandbox", "live"})
DELIVERY_BOOKING_COMMAND = "delivery.shipment.book"
DELIVERY_COMMAND_PROVIDER = "delivery"
DELIVERY_BOOKING_RECONCILIATION_DECISIONS = frozenset({"confirmed", "not_booked"})


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


def delivery_provider_accepts_quotes(
    db: Session,
    provider_code: str,
    *,
    lock: bool = True,
) -> bool:
    """Return whether a provider may back a customer quote.

    Availability checks are serialized by default. Quote creation/acceptance and
    tariff activation therefore cannot observe a provider half-way through an
    administrative mode/active-state mutation. Callers that only need an
    advisory, non-transactional view may opt out explicitly with ``lock=False``.
    """
    try:
        return resolve_delivery_provider_mode(
            db,
            provider_code,
            lock=lock,
        ) != "disabled"
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


def reconcile_delivery_booking(
    db: Session,
    *,
    shipment_id: int,
    decision: str,
    external_id: str = "",
) -> ProviderCommand:
    normalized_decision = str(decision or "").strip().lower()
    if normalized_decision not in DELIVERY_BOOKING_RECONCILIATION_DECISIONS:
        raise DeliveryProviderConfigurationError(
            "Delivery booking reconciliation decision must be confirmed or not_booked"
        )

    command = delivery_booking_command(db, shipment_id, lock=True)
    if command is None:
        raise DeliveryProviderConfigurationError(
            "Delivery booking command does not exist"
        )
    if command.status != "review_required":
        raise DeliveryProviderConfigurationError(
            "Only review_required delivery booking can be reconciled"
        )

    command.lease_token = None
    command.next_attempt_at = None
    command.completed_at = None

    if normalized_decision == "confirmed":
        provider_booking_id = str(external_id or "").strip()
        if len(provider_booking_id) < 3 or len(provider_booking_id) > 255:
            raise DeliveryProviderConfigurationError(
                "Confirmed delivery booking requires a valid provider booking id"
            )
        command.status = "sent"
        command.external_id = provider_booking_id
        command.last_error = ""
        command.completed_at = utcnow_naive()
    else:
        # An operator has established that the carrier did not create a booking.
        # Only then is an automatic retry safe; keep the same durable idempotency
        # key and attempt history rather than minting a second command.
        command.status = "pending"
        command.external_id = ""
        command.last_error = ""
        command.next_attempt_at = utcnow_naive()

    db.flush()
    return command
