import json
from decimal import Decimal

from sqlalchemy.orm import Session

from ..database import utcnow_naive
from ..delivery_models import DeliveryShipmentAuthority
from ..models import DeliveryShipment, Order
from .delivery_authority import (
    DeliveryAuthorityError,
    accepted_quote_for_order,
    bind_shipment_commercial_authority,
)
from .moysklad_outbound import enqueue_moysklad_demand
from .notifications import queue_order_status

_SHIPMENT_TRANSITIONS = {
    "created": {"shipped"},
    "shipped": {"delivered", "delivery_failed"},
    "delivery_failed": {"shipped", "returning"},
    "returning": {"returned_to_sender"},
    "delivered": set(),
    "returned_to_sender": set(),
}


def calculate_delivery_price(*_args, **_kwargs) -> Decimal:
    """Hard fail for the retired second tariff source.

    Commercial delivery price is created by DeliveryQuote and accepted by the
    Order. Shipment code must never recompute a tariff from provider defaults.
    """
    raise RuntimeError("Delivery price is quote-authoritative; request a DeliveryQuote")


def create_shipment(
    db: Session,
    order: Order,
    provider_code: str = "",
) -> DeliveryShipment:
    quote = accepted_quote_for_order(db, order.id, lock=True)
    requested_provider = str(provider_code or "").strip().lower()
    if requested_provider and requested_provider != quote.provider_code:
        raise ValueError("Shipment provider must match the accepted delivery quote")

    shipment = DeliveryShipment(
        order_id=order.id,
        provider_code=quote.provider_code,
        tracking_number="",
        status="created",
        price=quote.price,
        raw_payload=json.dumps(
            {
                "quote_public_id": quote.public_id,
                "provider": quote.provider_code,
                "service": quote.service_code,
                "currency": quote.currency,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
    )
    db.add(shipment)
    db.flush()
    try:
        bind_shipment_commercial_authority(
            db,
            shipment=shipment,
            order=order,
            quote=quote,
        )
    except DeliveryAuthorityError as exc:
        raise ValueError(str(exc)) from exc
    return shipment


def ensure_ready_shipment(
    db: Session,
    order: Order,
    provider_code: str = "",
) -> tuple[DeliveryShipment, bool]:
    requested_provider = str(provider_code or "").strip().lower()
    if requested_provider and len(requested_provider) > 64:
        raise ValueError("Delivery provider code is invalid")
    if order.status != "ready" or order.delivery_status != "ready":
        raise ValueError("Only a ready order can be transferred to delivery")

    quote = accepted_quote_for_order(db, order.id, lock=True)
    if requested_provider and requested_provider != quote.provider_code:
        raise ValueError("Shipment provider must match the accepted delivery quote")

    existing = (
        db.query(DeliveryShipment)
        .filter(DeliveryShipment.order_id == order.id)
        .order_by(DeliveryShipment.id.desc())
        .with_for_update()
        .first()
    )
    if existing:
        authority = (
            db.query(DeliveryShipmentAuthority)
            .filter(
                DeliveryShipmentAuthority.shipment_id == existing.id,
                DeliveryShipmentAuthority.order_id == order.id,
                DeliveryShipmentAuthority.quote_id == quote.id,
            )
            .first()
        )
        if authority is None:
            raise ValueError("Existing shipment lacks accepted quote commercial authority")
        if Decimal(str(existing.price)).quantize(Decimal("0.01")) != Decimal(str(quote.price)).quantize(Decimal("0.01")):
            raise ValueError("Existing shipment price differs from accepted delivery quote")
        return existing, False

    shipment = create_shipment(db, order, requested_provider)
    return shipment, True


def update_tracking(
    shipment: DeliveryShipment,
    tracking_number: str,
    status: str = "shipped",
) -> None:
    """Compatibility helper for internal imports that only mutate a shipment."""
    shipment.tracking_number = str(tracking_number or "").strip()
    shipment.status = str(status or "").strip().lower()
    shipment.updated_at = utcnow_naive()


def transition_shipment(
    db: Session,
    order: Order,
    shipment: DeliveryShipment,
    tracking_number: str,
    status: str = "shipped",
) -> Order:
    """Apply a shipment transition to an already Order-first locked root."""
    if int(shipment.order_id) != int(order.id):
        raise ValueError("Shipment changed while being updated")

    normalized_status = str(status or "").strip().lower()
    if normalized_status not in _SHIPMENT_TRANSITIONS:
        raise ValueError("Unsupported shipment status")
    if normalized_status == shipment.status:
        return order

    allowed = _SHIPMENT_TRANSITIONS.get(shipment.status, set())
    if normalized_status not in allowed:
        raise ValueError(
            f"Shipment transition {shipment.status} -> {normalized_status} is not allowed"
        )

    if normalized_status == "shipped":
        normalized_tracking = str(tracking_number or shipment.tracking_number or "").strip()
        if len(normalized_tracking) < 3:
            raise ValueError("Tracking number is required before shipment")
        if len(normalized_tracking) > 255:
            raise ValueError("Tracking number is too long")
        if shipment.status == "created" and (order.status != "ready" or order.delivery_status != "ready"):
            raise ValueError("Only a ready order can be shipped")
        update_tracking(shipment, normalized_tracking, "shipped")
        order.status = "shipped"
        order.delivery_status = "shipped"
        order.tracking_number = normalized_tracking
        enqueue_moysklad_demand(db, order.id)
    elif normalized_status == "delivered":
        if shipment.status != "shipped" or not shipment.tracking_number.strip():
            raise ValueError("Only a tracked shipment can be marked delivered")
        if order.status != "shipped" or order.delivery_status != "shipped":
            raise ValueError("Only a shipped order can be completed")
        update_tracking(shipment, shipment.tracking_number, "delivered")
        order.status = "completed"
        order.delivery_status = "delivered"
        order.tracking_number = shipment.tracking_number
    elif normalized_status == "delivery_failed":
        if shipment.status != "shipped":
            raise ValueError("Only a shipped order can record delivery failure")
        update_tracking(shipment, shipment.tracking_number, "delivery_failed")
        order.delivery_status = "delivery_failed"
    elif normalized_status == "returning":
        if shipment.status != "delivery_failed":
            raise ValueError("Only a failed delivery can return to sender")
        update_tracking(shipment, shipment.tracking_number, "returning")
        order.delivery_status = "returning"
    elif normalized_status == "returned_to_sender":
        if shipment.status != "returning":
            raise ValueError("Only a returning shipment can be received by sender")
        update_tracking(shipment, shipment.tracking_number, "returned_to_sender")
        order.delivery_status = "returned_to_sender"

    queue_order_status(db, order)
    return order
