from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import DeliveryShipment, Order


def _load_delivery_order_id(db: Session, shipment_id: int) -> int | None:
    row = (
        db.query(DeliveryShipment.order_id)
        .filter(DeliveryShipment.id == shipment_id)
        .first()
    )
    return int(row[0]) if row else None


def _select_delivery_order_for_update(db: Session, order_id: int) -> Order | None:
    return db.query(Order).filter(Order.id == order_id).with_for_update().first()


def _select_delivery_shipment_for_update(
    db: Session,
    shipment_id: int,
) -> DeliveryShipment | None:
    return (
        db.query(DeliveryShipment)
        .filter(DeliveryShipment.id == shipment_id)
        .with_for_update()
        .first()
    )


def lock_delivery_shipment_for_update(
    db: Session,
    shipment_id: int,
) -> tuple[Order, DeliveryShipment]:
    """Lock a delivery transition root as Order -> DeliveryShipment.

    The first read discovers only the shipment's order id and intentionally
    does not lock the shipment. Order is the canonical root lock. The shipment
    is locked second and its relationship is revalidated so concurrent or
    corrupt reassignment fails closed instead of reintroducing
    DeliveryShipment -> Order locking.
    """

    expected_order_id = _load_delivery_order_id(db, shipment_id)
    if expected_order_id is None:
        raise HTTPException(status_code=404, detail="Shipment not found")

    order = _select_delivery_order_for_update(db, expected_order_id)
    if not order:
        raise HTTPException(
            status_code=409,
            detail="Shipment is linked to a missing order",
        )

    shipment = _select_delivery_shipment_for_update(db, shipment_id)
    if not shipment:
        raise HTTPException(
            status_code=409,
            detail="Shipment changed while being locked",
        )
    if int(shipment.order_id) != expected_order_id:
        raise HTTPException(
            status_code=409,
            detail="Shipment changed while being locked",
        )
    return order, shipment
