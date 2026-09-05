import json
from decimal import Decimal

from sqlalchemy.orm import Session

from ..database import utcnow_naive
from ..models import DeliveryShipment, Order
from .moysklad_outbound import enqueue_moysklad_demand
from .notifications import queue_order_status

_SHIPMENT_TRANSITIONS = {
    "created": {"shipped"},
    "shipped": {"delivered"},
    "delivered": set(),
}


def calculate_delivery_price(provider_code: str, zone: str = "default") -> Decimal:
    base = {
        "courier": Decimal("500.00"),
        "cdek": Decimal("700.00"),
        "boxberry": Decimal("650.00"),
        "pickup": Decimal("0.00"),
    }
    return base.get(provider_code, Decimal("500.00"))


def create_shipment(db: Session, order: Order, provider_code: str = "courier") -> DeliveryShipment:
    """Create one shipment row without applying the HTTP workflow boundary.

    Kept as a low-level compatibility helper for internal callers. Production
    routes use ``ensure_ready_shipment`` so readiness and idempotency are always
    enforced at the external mutation boundary.
    """
    normalized_provider = str(provider_code or "courier").strip().lower()
    price = calculate_delivery_price(normalized_provider)
    shipment = DeliveryShipment(
        order_id=order.id,
        provider_code=normalized_provider,
        tracking_number="",
        status="created",
        price=price,
        raw_payload=json.dumps({"provider": normalized_provider}, ensure_ascii=False),
    )
    db.add(shipment)
    return shipment


def ensure_ready_shipment(
    db: Session,
    order: Order,
    provider_code: str = "courier",
) -> tuple[DeliveryShipment, bool]:
    normalized_provider = str(provider_code or "courier").strip().lower()
    if not normalized_provider or len(normalized_provider) > 64:
        raise ValueError("Delivery provider code is invalid")
    if order.status != "ready" or order.delivery_status != "ready":
        raise ValueError("Only a ready order can be transferred to delivery")

    existing = (
        db.query(DeliveryShipment)
        .filter(DeliveryShipment.order_id == order.id)
        .order_by(DeliveryShipment.id.desc())
        .with_for_update()
        .first()
    )
    if existing:
        return existing, False

    shipment = create_shipment(db, order, normalized_provider)
    db.flush()
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
    shipment: DeliveryShipment,
    tracking_number: str,
    status: str = "shipped",
) -> Order:
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in _SHIPMENT_TRANSITIONS:
        raise ValueError("Unsupported shipment status")
    if normalized_status == shipment.status:
        order = db.query(Order).filter(Order.id == shipment.order_id).with_for_update().first()
        if not order:
            raise ValueError("Shipment is linked to a missing order")
        return order

    allowed = _SHIPMENT_TRANSITIONS.get(shipment.status, set())
    if normalized_status not in allowed:
        raise ValueError(
            f"Shipment transition {shipment.status} -> {normalized_status} is not allowed"
        )

    order = (
        db.query(Order)
        .filter(Order.id == shipment.order_id)
        .with_for_update()
        .first()
    )
    if not order:
        raise ValueError("Shipment is linked to a missing order")

    if normalized_status == "shipped":
        normalized_tracking = str(tracking_number or "").strip()
        if len(normalized_tracking) < 3:
            raise ValueError("Tracking number is required before shipment")
        if len(normalized_tracking) > 255:
            raise ValueError("Tracking number is too long")
        if order.status != "ready" or order.delivery_status != "ready":
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

    queue_order_status(db, order)
    return order
