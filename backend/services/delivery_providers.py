import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..models import DeliveryProvider, DeliveryShipment, Order
from .notifications import queue_order_status

_MONEY_STEP = Decimal("0.01")
_PROVIDER_CODE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_BUILTIN_PROVIDERS = frozenset({"courier", "cdek", "boxberry", "pickup"})
_TRACKING_REQUIRED_STATUSES = frozenset(
    {"shipped", "delivery_failed", "delivered", "returned"}
)
_TRANSITIONS = {
    "created": {"shipped", "cancelled"},
    "shipped": {"delivery_failed", "delivered", "returned"},
    "delivery_failed": {"shipped", "returned"},
    "delivered": set(),
    "returned": set(),
    "cancelled": set(),
}


def normalize_provider_code(value: str) -> str:
    code = str(value or "").strip().lower()
    if not _PROVIDER_CODE.fullmatch(code):
        raise HTTPException(status_code=400, detail="Invalid delivery provider code")
    return code


def normalize_tracking_number(value: str) -> str:
    tracking = str(value or "").strip()
    if len(tracking) > 255:
        raise HTTPException(status_code=400, detail="Tracking number is too long")
    return tracking


def _delivery_lock_key(order_id: int) -> int:
    digest = hashlib.sha256(f"delivery-order:{order_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _acquire_delivery_lock(db: Session, order_id: int) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": _delivery_lock_key(order_id)},
    )


def _charged_delivery_price(order: Order) -> Decimal:
    try:
        amount = Decimal(str(order.delivery_price)).quantize(
            _MONEY_STEP,
            rounding=ROUND_HALF_UP,
        )
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(status_code=409, detail="Order delivery price is invalid")
    if not amount.is_finite() or amount < 0:
        raise HTTPException(status_code=409, detail="Order delivery price is invalid")
    return amount


def _require_provider(db: Session, provider_code: str) -> None:
    provider = (
        db.query(DeliveryProvider)
        .filter(DeliveryProvider.code == provider_code)
        .with_for_update()
        .first()
    )
    if provider:
        if not provider.active:
            raise HTTPException(status_code=409, detail="Delivery provider is inactive")
        return
    if provider_code not in _BUILTIN_PROVIDERS:
        raise HTTPException(status_code=404, detail="Delivery provider not found")


def _require_order_ready_for_shipment(order: Order) -> None:
    if order.payment_status not in {"paid", "partially_refunded"}:
        raise HTTPException(status_code=409, detail="Only a paid order can be shipped")
    if order.status != "ready" or order.delivery_status != "ready":
        raise HTTPException(status_code=409, detail="Order must be ready before shipment creation")
    if order.status in {"cancelled", "refunded", "refund_requested"}:
        raise HTTPException(status_code=409, detail="Order cannot be shipped in its current state")


def create_shipment(
    db: Session,
    order_id: int,
    provider_code: str = "courier",
) -> DeliveryShipment:
    normalized_provider = normalize_provider_code(provider_code)
    _acquire_delivery_lock(db, order_id)

    order = (
        db.query(Order)
        .filter(Order.id == order_id)
        .with_for_update()
        .first()
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    existing = (
        db.query(DeliveryShipment)
        .filter(DeliveryShipment.order_id == order.id)
        .with_for_update()
        .first()
    )
    if existing and existing.status != "cancelled":
        return existing

    _require_order_ready_for_shipment(order)
    _require_provider(db, normalized_provider)
    price = _charged_delivery_price(order)
    payload = json.dumps(
        {
            "provider": normalized_provider,
            "charged_delivery_price": f"{price:.2f}",
            "order_delivery_type": order.delivery_type,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    if existing:
        existing.provider_code = normalized_provider
        existing.tracking_number = ""
        existing.status = "created"
        existing.price = float(price)
        existing.raw_payload = payload
        existing.updated_at = datetime.utcnow()
        return existing

    shipment = DeliveryShipment(
        order_id=order.id,
        provider_code=normalized_provider,
        tracking_number="",
        status="created",
        price=float(price),
        raw_payload=payload,
    )
    db.add(shipment)
    db.flush()
    return shipment


def update_tracking(
    db: Session,
    shipment_id: int,
    tracking_number: str,
    status: str = "shipped",
) -> DeliveryShipment:
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in _TRANSITIONS:
        raise HTTPException(status_code=400, detail="Unsupported shipment status")
    normalized_tracking = normalize_tracking_number(tracking_number)
    if normalized_status in _TRACKING_REQUIRED_STATUSES and not normalized_tracking:
        raise HTTPException(status_code=400, detail="Tracking number is required for this shipment status")

    shipment = (
        db.query(DeliveryShipment)
        .filter(DeliveryShipment.id == shipment_id)
        .with_for_update()
        .first()
    )
    if not shipment:
        raise HTTPException(status_code=404, detail="Shipment not found")
    _acquire_delivery_lock(db, shipment.order_id)

    order = (
        db.query(Order)
        .filter(Order.id == shipment.order_id)
        .with_for_update()
        .first()
    )
    if not order:
        raise HTTPException(status_code=409, detail="Shipment order is missing")

    current_status = str(shipment.status or "").strip().lower()
    if current_status not in _TRANSITIONS:
        raise HTTPException(status_code=409, detail="Stored shipment status is invalid")

    if normalized_status == current_status:
        if normalized_tracking and shipment.tracking_number not in {"", normalized_tracking}:
            raise HTTPException(status_code=409, detail="Tracking number cannot be rewritten")
        if normalized_tracking and not shipment.tracking_number:
            shipment.tracking_number = normalized_tracking
            order.tracking_number = normalized_tracking
            shipment.updated_at = datetime.utcnow()
        return shipment

    if normalized_status not in _TRANSITIONS[current_status]:
        raise HTTPException(
            status_code=409,
            detail=f"Shipment transition {current_status} -> {normalized_status} is not allowed",
        )
    if shipment.tracking_number and normalized_tracking and shipment.tracking_number != normalized_tracking:
        raise HTTPException(status_code=409, detail="Tracking number cannot be rewritten")

    if current_status == "created" and normalized_status == "shipped":
        if order.status != "ready" or order.delivery_status != "ready":
            raise HTTPException(status_code=409, detail="Only a ready order can be shipped")
        if order.payment_status not in {"paid", "partially_refunded"}:
            raise HTTPException(status_code=409, detail="Only a paid order can be shipped")

    shipment.tracking_number = normalized_tracking or shipment.tracking_number
    shipment.status = normalized_status
    shipment.updated_at = datetime.utcnow()

    if normalized_status == "shipped":
        order.status = "shipped"
        order.delivery_status = "shipped"
        order.tracking_number = shipment.tracking_number
    elif normalized_status == "delivery_failed":
        order.status = "shipped"
        order.delivery_status = "delivery_failed"
        order.tracking_number = shipment.tracking_number
    elif normalized_status == "delivered":
        order.status = "completed"
        order.delivery_status = "delivered"
        order.tracking_number = shipment.tracking_number
    elif normalized_status == "returned":
        order.status = "shipped"
        order.delivery_status = "returned"
        order.tracking_number = shipment.tracking_number
    elif normalized_status == "cancelled":
        order.status = "ready"
        order.delivery_status = "ready"
        order.tracking_number = ""
        shipment.tracking_number = ""

    queue_order_status(db, order)
    return shipment
