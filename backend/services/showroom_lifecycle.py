import json
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session, joinedload

from ..models import BusinessEvent, Order, OrderItem
from ..showroom_models import ShowroomAppointment
from .inventory import release_variant, reserve_variant


FITTING_REQUEST_TYPES = {"fitting", "preorder_fitting"}
RESERVING_STATUSES = {"confirmed", "checked_in", "fitting"}
RELEASING_STATUSES = {
    "requested",
    "reviewing",
    "proposed",
    "preordered",
    "completed",
    "cancelled",
    "no_show",
}
RESERVATION_GRACE = timedelta(hours=2)


def publish_showroom_event(
    db: Session,
    appointment: ShowroomAppointment,
    event_type: str,
    extra: dict | None = None,
) -> None:
    payload = {
        "appointment_id": appointment.id,
        "customer_id": appointment.customer_id,
        "product_id": appointment.product_id,
        "variant_id": appointment.variant_id,
        "showroom_id": appointment.showroom_id,
        "assigned_admin_id": appointment.assigned_admin_id,
        "linked_order_id": appointment.linked_order_id,
        "request_type": appointment.request_type,
        "status": appointment.status,
        "inventory_reserved": appointment.inventory_reserved,
        "reservation_expires_at": appointment.reservation_expires_at,
        **(extra or {}),
    }
    db.add(
        BusinessEvent(
            event_type=event_type,
            aggregate_type="showroom_appointment",
            aggregate_id=str(appointment.id),
            payload_json=json.dumps(payload, ensure_ascii=False, default=str),
            status="pending",
        )
    )


def reservation_expiry(appointment: ShowroomAppointment) -> datetime:
    start = appointment.confirmed_start or appointment.proposed_start or appointment.preferred_start
    return start + timedelta(minutes=appointment.duration_minutes) + RESERVATION_GRACE


def reserve_appointment_inventory(db: Session, appointment: ShowroomAppointment) -> bool:
    if appointment.request_type not in FITTING_REQUEST_TYPES:
        return False
    if appointment.linked_order_id is not None:
        return False
    if appointment.variant_id is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "showroom_variant_required",
                "message": "Перед подтверждением примерки выберите конкретный размер и цвет",
                "appointment_id": appointment.id,
            },
        )

    expires_at = reservation_expiry(appointment)
    if appointment.inventory_reserved:
        appointment.reservation_expires_at = expires_at
        return False

    reserve_variant(db, appointment.variant_id, 1)
    now = datetime.utcnow()
    appointment.inventory_reserved = True
    appointment.inventory_reserved_at = now
    appointment.inventory_released_at = None
    appointment.reservation_expires_at = expires_at
    publish_showroom_event(
        db,
        appointment,
        "showroom.inventory_reserved",
        {"quantity": 1, "reserved_at": now, "expires_at": expires_at},
    )
    return True


def release_appointment_inventory(
    db: Session,
    appointment: ShowroomAppointment,
    *,
    reason: str,
) -> bool:
    if not appointment.inventory_reserved:
        appointment.reservation_expires_at = None
        return False

    if appointment.variant_id is not None:
        release_variant(db, appointment.variant_id, 1)
    released_at = datetime.utcnow()
    appointment.inventory_reserved = False
    appointment.inventory_released_at = released_at
    appointment.reservation_expires_at = None
    publish_showroom_event(
        db,
        appointment,
        "showroom.inventory_released",
        {"quantity": 1, "released_at": released_at, "reason": reason},
    )
    return True


def validate_linked_order(
    db: Session,
    appointment: ShowroomAppointment,
    order_id: int,
) -> Order:
    order = (
        db.query(Order)
        .options(joinedload(Order.items))
        .filter(Order.id == order_id, Order.customer_id == appointment.customer_id)
        .first()
    )
    if not order:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "showroom_order_not_found",
                "message": "Заказ не найден или принадлежит другому клиенту",
            },
        )

    item_query = [item for item in order.items if item.product_id == appointment.product_id]
    if appointment.variant_id is not None:
        item_query = [item for item in item_query if item.variant_id == appointment.variant_id]
    if not item_query:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "showroom_order_item_mismatch",
                "message": "В заказе нет товара или варианта из этой примерки",
                "order_id": order.id,
                "product_id": appointment.product_id,
                "variant_id": appointment.variant_id,
            },
        )
    return order


def link_appointment_order(
    db: Session,
    appointment: ShowroomAppointment,
    order_id: int,
) -> Order:
    order = validate_linked_order(db, appointment, order_id)
    if appointment.linked_order_id == order.id:
        return order

    previous_order_id = appointment.linked_order_id
    release_appointment_inventory(db, appointment, reason="converted_to_order")
    appointment.linked_order_id = order.id
    publish_showroom_event(
        db,
        appointment,
        "showroom.order_linked",
        {"previous_order_id": previous_order_id, "order_id": order.id},
    )
    return order


def sync_appointment_reservation(
    db: Session,
    appointment: ShowroomAppointment,
    *,
    previous_status: str | None = None,
) -> None:
    if appointment.status in RESERVING_STATUSES:
        reserve_appointment_inventory(db, appointment)
    elif appointment.status == "purchased":
        if appointment.linked_order_id is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "showroom_order_required",
                    "message": "Перед переводом в статус purchased свяжите примерку с заказом",
                    "appointment_id": appointment.id,
                },
            )
        release_appointment_inventory(db, appointment, reason="purchased")
    elif appointment.status in RELEASING_STATUSES:
        release_appointment_inventory(db, appointment, reason=f"status:{appointment.status}")

    if previous_status is not None and previous_status != appointment.status:
        publish_showroom_event(
            db,
            appointment,
            f"showroom.status.{appointment.status}",
            {"previous_status": previous_status},
        )


def expire_showroom_reservations(db: Session, *, now: datetime | None = None) -> int:
    effective_now = now or datetime.utcnow()
    rows = (
        db.query(ShowroomAppointment)
        .filter(
            ShowroomAppointment.inventory_reserved.is_(True),
            ShowroomAppointment.reservation_expires_at.is_not(None),
            ShowroomAppointment.reservation_expires_at <= effective_now,
            ShowroomAppointment.linked_order_id.is_(None),
        )
        .with_for_update()
        .all()
    )

    for appointment in rows:
        previous_status = appointment.status
        release_appointment_inventory(db, appointment, reason="expired")
        if appointment.status == "confirmed":
            appointment.status = "no_show"
        appointment.updated_at = effective_now
        publish_showroom_event(
            db,
            appointment,
            "showroom.reservation_expired",
            {
                "previous_status": previous_status,
                "effective_status": appointment.status,
                "expired_at": effective_now,
            },
        )

    return len(rows)
