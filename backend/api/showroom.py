import json
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..models import AdminUser, Customer, CustomerTimelineEvent, Product, ProductVariant
from ..security import get_current_admin, get_current_customer
from ..services.audit import log_admin_action
from ..services.notifications import queue_notification
from ..services.rbac import require_permission
from ..services.showroom_lifecycle import (
    link_appointment_order,
    publish_showroom_event,
    release_appointment_inventory,
    sync_appointment_reservation,
)
from ..showroom_models import (
    ProductShowroomProfile,
    ShowroomAppointment,
    ShowroomAppointmentMessage,
    ShowroomLocation,
)
from ..showroom_schemas import (
    ProductShowroomProfileIn,
    ProductShowroomProfileOut,
    ShowroomAppointmentCreate,
    ShowroomAppointmentMessageCreate,
    ShowroomAppointmentOut,
    ShowroomAppointmentUpdate,
    ShowroomCustomerAction,
    ShowroomLocationCreate,
    ShowroomLocationOut,
)

router = APIRouter(prefix="/showroom", tags=["showroom"])

TERMINAL_STATUSES = {"purchased", "completed", "cancelled", "no_show"}
BLOCKING_STATUSES = {"confirmed", "checked_in", "fitting"}
ALLOWED_ADMIN_TRANSITIONS = {
    "requested": {"reviewing", "proposed", "confirmed", "cancelled"},
    "reviewing": {"proposed", "confirmed", "cancelled"},
    "proposed": {"reviewing", "confirmed", "cancelled"},
    "confirmed": {"checked_in", "cancelled", "no_show"},
    "checked_in": {"fitting", "completed", "cancelled"},
    "fitting": {"purchased", "preordered", "completed"},
    "preordered": {"purchased", "completed", "cancelled"},
    "purchased": {"completed"},
    "completed": set(),
    "cancelled": set(),
    "no_show": {"requested"},
}
WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MIN_APPOINTMENT_NOTICE = timedelta(minutes=30)


def _utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _customer_name(customer: Customer) -> str:
    return (
        " ".join(filter(None, [customer.first_name, customer.last_name])).strip()
        or customer.username
        or f"Клиент #{customer.id}"
    )


def _appointment_query(db: Session):
    return db.query(ShowroomAppointment).options(
        joinedload(ShowroomAppointment.messages),
        joinedload(ShowroomAppointment.product).joinedload(Product.images),
        joinedload(ShowroomAppointment.variant),
        joinedload(ShowroomAppointment.customer),
        joinedload(ShowroomAppointment.showroom),
        joinedload(ShowroomAppointment.assigned_admin),
        joinedload(ShowroomAppointment.linked_order),
    )


def _serialize(appointment: ShowroomAppointment) -> dict:
    product_images = (
        sorted(appointment.product.images, key=lambda image: image.sort_order)
        if appointment.product
        else []
    )
    linked_order = appointment.linked_order
    return {
        "id": appointment.id,
        "customer_id": appointment.customer_id,
        "product_id": appointment.product_id,
        "variant_id": appointment.variant_id,
        "showroom_id": appointment.showroom_id,
        "assigned_admin_id": appointment.assigned_admin_id,
        "linked_order_id": appointment.linked_order_id,
        "request_type": appointment.request_type,
        "status": appointment.status,
        "preferred_start": appointment.preferred_start,
        "alternative_start": appointment.alternative_start,
        "proposed_start": appointment.proposed_start,
        "confirmed_start": appointment.confirmed_start,
        "duration_minutes": appointment.duration_minutes,
        "inventory_reserved": appointment.inventory_reserved,
        "inventory_reserved_at": appointment.inventory_reserved_at,
        "inventory_released_at": appointment.inventory_released_at,
        "reservation_expires_at": appointment.reservation_expires_at,
        "size": appointment.size,
        "color": appointment.color,
        "contact_phone": appointment.contact_phone or "",
        "customer_note": appointment.customer_note,
        "manager_note": appointment.manager_note,
        "source": appointment.source,
        "created_at": appointment.created_at,
        "updated_at": appointment.updated_at,
        "messages": appointment.messages,
        "product_title": appointment.product.title if appointment.product else "",
        "product_image_url": product_images[0].url if product_images else "",
        "showroom_name": appointment.showroom.name if appointment.showroom else "",
        "showroom_address": appointment.showroom.address if appointment.showroom else "",
        "customer_name": _customer_name(appointment.customer) if appointment.customer else "",
        "customer_telegram_id": appointment.customer.telegram_id if appointment.customer else "",
        "manager_email": appointment.assigned_admin.email if appointment.assigned_admin else "",
        "linked_order_status": linked_order.status if linked_order else "",
        "linked_order_payment_status": linked_order.payment_status if linked_order else "",
    }


def _timeline(
    db: Session,
    appointment: ShowroomAppointment,
    event_type: str,
    title: str,
    extra: dict | None = None,
) -> None:
    payload = {
        "appointment_id": appointment.id,
        "product_id": appointment.product_id,
        "variant_id": appointment.variant_id,
        "showroom_id": appointment.showroom_id,
        "linked_order_id": appointment.linked_order_id,
        "request_type": appointment.request_type,
        "status": appointment.status,
        "inventory_reserved": appointment.inventory_reserved,
        "reservation_expires_at": appointment.reservation_expires_at,
        **(extra or {}),
    }
    db.add(
        CustomerTimelineEvent(
            customer_id=appointment.customer_id,
            event_type=event_type,
            title=title,
            payload=json.dumps(payload, ensure_ascii=False, default=str),
        )
    )


def _notification_text(appointment: ShowroomAppointment, prefix: str) -> str:
    when = appointment.confirmed_start or appointment.proposed_start or appointment.preferred_start
    when_text = when.strftime("%d.%m.%Y в %H:%M") if when else "время уточняется"
    location = appointment.showroom.name if appointment.showroom else "шоурум FLASHIN"
    return (
        f"{prefix}\n{appointment.product.title}\n{when_text} · {location}\n"
        "Откройте Mini App, чтобы посмотреть детали и ответить менеджеру."
    )


def _load_owned(db: Session, appointment_id: int, customer_id: int) -> ShowroomAppointment:
    appointment = (
        _appointment_query(db)
        .filter(
            ShowroomAppointment.id == appointment_id,
            ShowroomAppointment.customer_id == customer_id,
        )
        .first()
    )
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return appointment


def _load_admin(db: Session, appointment_id: int) -> ShowroomAppointment:
    appointment = _appointment_query(db).filter(ShowroomAppointment.id == appointment_id).first()
    if not appointment:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return appointment


def _ensure_future(value: datetime, field_name: str) -> None:
    if value < datetime.utcnow() + MIN_APPOINTMENT_NOTICE:
        raise HTTPException(
            status_code=400,
            detail=f"{field_name} должно быть не менее чем через 30 минут",
        )


def _ensure_within_opening_hours(
    showroom: ShowroomLocation | None,
    start: datetime,
    duration_minutes: int,
) -> None:
    if showroom is None or not showroom.opening_hours_json or showroom.opening_hours_json == "{}":
        return
    try:
        schedule = json.loads(showroom.opening_hours_json)
        showroom_timezone = ZoneInfo(showroom.timezone)
    except (json.JSONDecodeError, ZoneInfoNotFoundError) as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "showroom_schedule_invalid",
                "message": "Расписание шоурума настроено некорректно",
                "showroom_id": showroom.id,
            },
        ) from exc

    local_start = start.replace(tzinfo=timezone.utc).astimezone(showroom_timezone)
    local_end = local_start + timedelta(minutes=duration_minutes)
    day_key = WEEKDAY_KEYS[local_start.weekday()]
    intervals = schedule.get(day_key) or []

    for opening, closing in intervals:
        opening_time = time.fromisoformat(opening)
        closing_time = time.fromisoformat(closing)
        interval_start = datetime.combine(local_start.date(), opening_time, showroom_timezone)
        interval_end = datetime.combine(local_start.date(), closing_time, showroom_timezone)
        if interval_end <= interval_start:
            interval_end += timedelta(days=1)
        if local_start >= interval_start and local_end <= interval_end:
            return

    raise HTTPException(
        status_code=409,
        detail={
            "code": "showroom_closed",
            "message": "Выбранное время находится вне часов работы шоурума",
            "showroom_id": showroom.id,
            "local_start": local_start.isoformat(),
            "duration_minutes": duration_minutes,
        },
    )


def _check_conflict(
    db: Session,
    appointment: ShowroomAppointment,
    start: datetime,
    duration_minutes: int,
) -> None:
    resource_filters = []
    if appointment.showroom_id is not None:
        resource_filters.append(ShowroomAppointment.showroom_id == appointment.showroom_id)
    if appointment.assigned_admin_id is not None:
        resource_filters.append(
            ShowroomAppointment.assigned_admin_id == appointment.assigned_admin_id
        )
    if not resource_filters:
        raise HTTPException(
            status_code=400,
            detail="Перед подтверждением выберите шоурум или ответственного менеджера",
        )

    end = start + timedelta(minutes=duration_minutes)
    candidates = (
        db.query(ShowroomAppointment)
        .filter(
            ShowroomAppointment.id != appointment.id,
            ShowroomAppointment.status.in_(BLOCKING_STATUSES),
            ShowroomAppointment.confirmed_start.is_not(None),
            or_(*resource_filters),
        )
        .all()
    )
    for row in candidates:
        row_start = row.confirmed_start
        row_end = row_start + timedelta(minutes=row.duration_minutes)
        if start < row_end and row_start < end:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "showroom_slot_conflict",
                    "message": "Выбранное время пересекается с другой подтверждённой записью",
                    "conflicting_appointment_id": row.id,
                    "conflicting_start": row_start.isoformat(),
                    "conflicting_end": row_end.isoformat(),
                },
            )


def _validate_variant(
    db: Session,
    product_id: int,
    variant_id: int,
) -> ProductVariant:
    variant = (
        db.query(ProductVariant)
        .filter(
            ProductVariant.id == variant_id,
            ProductVariant.product_id == product_id,
        )
        .first()
    )
    if not variant:
        raise HTTPException(status_code=400, detail="Variant does not belong to product")
    return variant


@router.get("/locations", response_model=list[ShowroomLocationOut])
def list_locations(db: Session = Depends(get_db)):
    return (
        db.query(ShowroomLocation)
        .filter(ShowroomLocation.active.is_(True))
        .order_by(ShowroomLocation.city, ShowroomLocation.name)
        .all()
    )


@router.get("/products/{product_id}/availability")
def product_availability(product_id: int, db: Session = Depends(get_db)):
    product = (
        db.query(Product)
        .options(joinedload(Product.variants))
        .filter(Product.id == product_id, Product.active.is_(True))
        .first()
    )
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    profile = (
        db.query(ProductShowroomProfile)
        .filter(ProductShowroomProfile.product_id == product_id)
        .first()
    )
    total_available = sum(variant.available_qty for variant in product.variants)
    if not profile:
        return {
            "product_id": product.id,
            "availability_status": "in_stock" if total_available > 0 else "unavailable",
            "preorder_enabled": False,
            "fitting_enabled": total_available > 0,
            "expected_at": None,
            "showroom_note": "",
        }
    return profile


@router.post("/appointments", response_model=ShowroomAppointmentOut)
def create_appointment(
    payload: ShowroomAppointmentCreate,
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    preferred_start = _utc_naive(payload.preferred_start)
    alternative_start = _utc_naive(payload.alternative_start)
    _ensure_future(preferred_start, "Предпочтительное время")
    if alternative_start is not None:
        _ensure_future(alternative_start, "Альтернативное время")

    product = (
        db.query(Product)
        .options(joinedload(Product.variants), joinedload(Product.images))
        .filter(Product.id == payload.product_id, Product.active.is_(True))
        .first()
    )
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    variant = (
        _validate_variant(db, product.id, payload.variant_id)
        if payload.variant_id is not None
        else None
    )

    profile = (
        db.query(ProductShowroomProfile)
        .filter(ProductShowroomProfile.product_id == product.id)
        .first()
    )
    fitting_enabled = (
        profile.fitting_enabled
        if profile
        else any(variant_row.available_qty > 0 for variant_row in product.variants)
    )
    preorder_enabled = profile.preorder_enabled if profile else False
    if payload.request_type in {"fitting", "preorder_fitting"} and not fitting_enabled:
        raise HTTPException(status_code=409, detail="Примерка для этого товара пока недоступна")
    if payload.request_type in {"preorder", "preorder_fitting"} and not preorder_enabled:
        raise HTTPException(status_code=409, detail="Предзаказ для этого товара пока недоступен")

    showroom = None
    if payload.showroom_id is not None:
        showroom = (
            db.query(ShowroomLocation)
            .filter(
                ShowroomLocation.id == payload.showroom_id,
                ShowroomLocation.active.is_(True),
            )
            .first()
        )
        if not showroom:
            raise HTTPException(status_code=404, detail="Showroom not found")
        _ensure_within_opening_hours(showroom, preferred_start, payload.duration_minutes)
        if alternative_start is not None:
            _ensure_within_opening_hours(showroom, alternative_start, payload.duration_minutes)

    duplicate = (
        db.query(ShowroomAppointment)
        .filter(
            ShowroomAppointment.customer_id == customer.id,
            ShowroomAppointment.product_id == product.id,
            ShowroomAppointment.status.notin_(TERMINAL_STATUSES),
        )
        .first()
    )
    if duplicate:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "active_appointment_exists",
                "message": "По этому товару уже есть активная заявка",
                "appointment_id": duplicate.id,
            },
        )

    appointment = ShowroomAppointment(
        customer_id=customer.id,
        product_id=product.id,
        variant_id=variant.id if variant else None,
        showroom_id=payload.showroom_id,
        request_type=payload.request_type,
        status="requested",
        preferred_start=preferred_start,
        alternative_start=alternative_start,
        duration_minutes=payload.duration_minutes,
        size=payload.size or (variant.size if variant else ""),
        color=payload.color or (variant.color if variant else ""),
        contact_phone=payload.contact_phone or customer.phone or "",
        customer_note=payload.customer_note,
    )
    db.add(appointment)
    db.flush()
    if payload.customer_note.strip():
        db.add(
            ShowroomAppointmentMessage(
                appointment_id=appointment.id,
                sender_type="customer",
                sender_customer_id=customer.id,
                body=payload.customer_note.strip(),
            )
        )
    _timeline(
        db,
        appointment,
        "showroom.appointment_requested",
        "Заявка на примерку или предзаказ создана",
    )
    publish_showroom_event(db, appointment, "showroom.appointment_requested")
    db.commit()
    return _serialize(_load_owned(db, appointment.id, customer.id))


@router.get("/appointments", response_model=list[ShowroomAppointmentOut])
def my_appointments(
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    rows = (
        _appointment_query(db)
        .filter(ShowroomAppointment.customer_id == customer.id)
        .order_by(ShowroomAppointment.created_at.desc())
        .all()
    )
    return [_serialize(row) for row in rows]


@router.get("/appointments/{appointment_id}", response_model=ShowroomAppointmentOut)
def my_appointment(
    appointment_id: int,
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    return _serialize(_load_owned(db, appointment_id, customer.id))


@router.post("/appointments/{appointment_id}/messages", response_model=ShowroomAppointmentOut)
def customer_message(
    appointment_id: int,
    payload: ShowroomAppointmentMessageCreate,
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    appointment = _load_owned(db, appointment_id, customer.id)
    if appointment.status in TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="Переписка по завершённой заявке закрыта")
    db.add(
        ShowroomAppointmentMessage(
            appointment_id=appointment.id,
            sender_type="customer",
            sender_customer_id=customer.id,
            body=payload.body.strip(),
        )
    )
    appointment.updated_at = datetime.utcnow()
    _timeline(db, appointment, "showroom.customer_message", "Клиент написал менеджеру")
    publish_showroom_event(
        db,
        appointment,
        "showroom.customer_message",
        {"message_length": len(payload.body.strip())},
    )
    db.commit()
    return _serialize(_load_owned(db, appointment.id, customer.id))


@router.post("/appointments/{appointment_id}/action", response_model=ShowroomAppointmentOut)
def customer_action(
    appointment_id: int,
    payload: ShowroomCustomerAction,
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    appointment = _load_owned(db, appointment_id, customer.id)
    previous_status = appointment.status

    if payload.action == "accept_proposal":
        if appointment.status != "proposed" or not appointment.proposed_start:
            raise HTTPException(status_code=409, detail="Нет предложения времени для подтверждения")
        if not appointment.showroom:
            raise HTTPException(status_code=409, detail="Менеджер не указал шоурум")
        _ensure_future(appointment.proposed_start, "Предложенное время")
        _ensure_within_opening_hours(
            appointment.showroom,
            appointment.proposed_start,
            appointment.duration_minutes,
        )
        _check_conflict(
            db,
            appointment,
            appointment.proposed_start,
            appointment.duration_minutes,
        )
        appointment.confirmed_start = appointment.proposed_start
        appointment.status = "confirmed"
        sync_appointment_reservation(db, appointment, previous_status=previous_status)
        _timeline(
            db,
            appointment,
            "showroom.appointment_confirmed",
            "Клиент подтвердил время визита",
        )
        queue_notification(
            db,
            appointment.customer.telegram_id,
            _notification_text(appointment, "Ваша запись в шоурум подтверждена"),
        )
    elif payload.action == "request_reschedule":
        if appointment.status in TERMINAL_STATUSES:
            raise HTTPException(status_code=409, detail="Завершённую заявку нельзя перенести")
        preferred_start = _utc_naive(payload.preferred_start)
        alternative_start = _utc_naive(payload.alternative_start)
        _ensure_future(preferred_start, "Новое предпочтительное время")
        if alternative_start is not None:
            _ensure_future(alternative_start, "Новое альтернативное время")
        if appointment.showroom:
            _ensure_within_opening_hours(
                appointment.showroom,
                preferred_start,
                appointment.duration_minutes,
            )
            if alternative_start is not None:
                _ensure_within_opening_hours(
                    appointment.showroom,
                    alternative_start,
                    appointment.duration_minutes,
                )
        appointment.preferred_start = preferred_start
        appointment.alternative_start = alternative_start
        appointment.proposed_start = None
        appointment.confirmed_start = None
        appointment.status = "requested"
        sync_appointment_reservation(db, appointment, previous_status=previous_status)
        _timeline(
            db,
            appointment,
            "showroom.reschedule_requested",
            "Клиент запросил перенос встречи",
        )
    elif payload.action == "cancel":
        if appointment.status in TERMINAL_STATUSES:
            raise HTTPException(status_code=409, detail="Заявка уже завершена")
        appointment.status = "cancelled"
        sync_appointment_reservation(db, appointment, previous_status=previous_status)
        _timeline(
            db,
            appointment,
            "showroom.appointment_cancelled",
            "Клиент отменил заявку",
        )

    if payload.note.strip():
        db.add(
            ShowroomAppointmentMessage(
                appointment_id=appointment.id,
                sender_type="customer",
                sender_customer_id=customer.id,
                body=payload.note.strip(),
            )
        )
    appointment.updated_at = datetime.utcnow()
    db.commit()
    return _serialize(_load_owned(db, appointment.id, customer.id))


@router.get("/admin/appointments", response_model=list[ShowroomAppointmentOut])
def admin_appointments(
    status: str | None = None,
    assigned_to_me: bool = False,
    showroom_id: int | None = None,
    linked_order_id: int | None = None,
    inventory_reserved: bool | None = None,
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "appointments.read")
    query = _appointment_query(db)
    if status:
        query = query.filter(ShowroomAppointment.status == status)
    if assigned_to_me:
        query = query.filter(ShowroomAppointment.assigned_admin_id == admin.id)
    if showroom_id:
        query = query.filter(ShowroomAppointment.showroom_id == showroom_id)
    if linked_order_id:
        query = query.filter(ShowroomAppointment.linked_order_id == linked_order_id)
    if inventory_reserved is not None:
        query = query.filter(ShowroomAppointment.inventory_reserved.is_(inventory_reserved))
    if date_from:
        query = query.filter(ShowroomAppointment.preferred_start >= _utc_naive(date_from))
    if date_to:
        query = query.filter(ShowroomAppointment.preferred_start < _utc_naive(date_to))
    rows = query.order_by(ShowroomAppointment.updated_at.desc()).limit(500).all()
    return [_serialize(row) for row in rows]


@router.get("/admin/appointments/{appointment_id}", response_model=ShowroomAppointmentOut)
def admin_appointment(
    appointment_id: int,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "appointments.read")
    return _serialize(_load_admin(db, appointment_id))


@router.patch("/admin/appointments/{appointment_id}", response_model=ShowroomAppointmentOut)
def update_appointment(
    appointment_id: int,
    payload: ShowroomAppointmentUpdate,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "appointments.write")
    appointment = _load_admin(db, appointment_id)
    data = payload.model_dump(exclude_unset=True)
    previous_status = appointment.status

    if payload.status is not None:
        allowed = ALLOWED_ADMIN_TRANSITIONS.get(appointment.status, set())
        if payload.status != appointment.status and payload.status not in allowed:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "invalid_appointment_transition",
                    "from": appointment.status,
                    "to": payload.status,
                    "allowed": sorted(allowed),
                },
            )

    if "variant_id" in data and payload.variant_id != appointment.variant_id:
        if appointment.inventory_reserved:
            release_appointment_inventory(db, appointment, reason="variant_changed")
        if payload.variant_id is None:
            appointment.variant_id = None
            appointment.size = ""
            appointment.color = ""
        else:
            variant = _validate_variant(db, appointment.product_id, payload.variant_id)
            appointment.variant_id = variant.id
            appointment.size = variant.size
            appointment.color = variant.color

    if "showroom_id" in data:
        if payload.showroom_id is None:
            appointment.showroom_id = None
            appointment.showroom = None
        else:
            showroom = (
                db.query(ShowroomLocation)
                .filter(
                    ShowroomLocation.id == payload.showroom_id,
                    ShowroomLocation.active.is_(True),
                )
                .first()
            )
            if not showroom:
                raise HTTPException(status_code=404, detail="Showroom not found")
            appointment.showroom_id = showroom.id
            appointment.showroom = showroom

    if "assigned_admin_id" in data:
        if payload.assigned_admin_id is None:
            appointment.assigned_admin_id = None
            appointment.assigned_admin = None
        else:
            assignee = (
                db.query(AdminUser)
                .filter(
                    AdminUser.id == payload.assigned_admin_id,
                    AdminUser.active.is_(True),
                )
                .first()
            )
            if not assignee:
                raise HTTPException(status_code=404, detail="Assigned administrator not found")
            appointment.assigned_admin_id = assignee.id
            appointment.assigned_admin = assignee
    elif appointment.assigned_admin_id is None:
        appointment.assigned_admin_id = admin.id
        appointment.assigned_admin = admin

    if "proposed_start" in data:
        appointment.proposed_start = _utc_naive(payload.proposed_start)
        if appointment.proposed_start is not None:
            _ensure_future(appointment.proposed_start, "Предложенное время")

    if "confirmed_start" in data:
        appointment.confirmed_start = _utc_naive(payload.confirmed_start)
    if payload.duration_minutes is not None:
        appointment.duration_minutes = payload.duration_minutes
    if payload.manager_note is not None:
        appointment.manager_note = payload.manager_note

    if "linked_order_id" in data:
        if payload.linked_order_id is None:
            if appointment.status == "purchased" or payload.status == "purchased":
                raise HTTPException(
                    status_code=409,
                    detail="Нельзя отвязать заказ от купленной заявки",
                )
            previous_order_id = appointment.linked_order_id
            appointment.linked_order_id = None
            appointment.linked_order = None
            publish_showroom_event(
                db,
                appointment,
                "showroom.order_unlinked",
                {"previous_order_id": previous_order_id},
            )
        else:
            appointment.linked_order = link_appointment_order(
                db,
                appointment,
                payload.linked_order_id,
            )

    if payload.status is not None:
        appointment.status = payload.status

    if appointment.status == "proposed":
        if not appointment.proposed_start:
            raise HTTPException(
                status_code=400,
                detail="Для предложения клиенту необходимо указать proposed_start",
            )
        if not appointment.showroom_id:
            raise HTTPException(
                status_code=400,
                detail="Перед предложением времени выберите шоурум",
            )
        _ensure_within_opening_hours(
            appointment.showroom,
            appointment.proposed_start,
            appointment.duration_minutes,
        )

    if appointment.status == "confirmed":
        appointment.confirmed_start = (
            appointment.confirmed_start
            or appointment.proposed_start
            or appointment.preferred_start
        )
        if not appointment.showroom_id:
            raise HTTPException(status_code=400, detail="Перед подтверждением выберите шоурум")
        if previous_status != "confirmed" or "confirmed_start" in data:
            _ensure_future(appointment.confirmed_start, "Подтверждённое время")
        _ensure_within_opening_hours(
            appointment.showroom,
            appointment.confirmed_start,
            appointment.duration_minutes,
        )
        _check_conflict(
            db,
            appointment,
            appointment.confirmed_start,
            appointment.duration_minutes,
        )

    sync_appointment_reservation(db, appointment, previous_status=previous_status)
    appointment.updated_at = datetime.utcnow()

    if appointment.status != previous_status:
        _timeline(
            db,
            appointment,
            f"showroom.status.{appointment.status}",
            f"Статус заявки изменён: {appointment.status}",
            {"previous_status": previous_status, "admin_id": admin.id},
        )
        if appointment.status == "proposed":
            queue_notification(
                db,
                appointment.customer.telegram_id,
                _notification_text(appointment, "Менеджер предложил время примерки"),
            )
        elif appointment.status == "confirmed":
            queue_notification(
                db,
                appointment.customer.telegram_id,
                _notification_text(appointment, "Ваша запись в шоурум подтверждена"),
            )
        elif appointment.status == "cancelled":
            queue_notification(
                db,
                appointment.customer.telegram_id,
                _notification_text(appointment, "Запись отменена менеджером"),
            )
        elif appointment.status == "purchased":
            queue_notification(
                db,
                appointment.customer.telegram_id,
                f"Покупка по записи #{appointment.id} оформлена. Заказ #{appointment.linked_order_id}.",
            )

    publish_showroom_event(
        db,
        appointment,
        "showroom.appointment_updated",
        {"admin_id": admin.id, "changed_fields": sorted(data)},
    )
    log_admin_action(
        db,
        admin,
        "showroom.appointment.update",
        "showroom_appointment",
        appointment.id,
        data,
    )
    db.commit()
    return _serialize(_load_admin(db, appointment.id))


@router.post("/admin/appointments/{appointment_id}/messages", response_model=ShowroomAppointmentOut)
def manager_message(
    appointment_id: int,
    payload: ShowroomAppointmentMessageCreate,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "appointments.message")
    appointment = _load_admin(db, appointment_id)
    if appointment.status in TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="Переписка по завершённой заявке закрыта")
    if appointment.assigned_admin_id is None:
        appointment.assigned_admin_id = admin.id
    db.add(
        ShowroomAppointmentMessage(
            appointment_id=appointment.id,
            sender_type="admin",
            sender_admin_id=admin.id,
            body=payload.body.strip(),
        )
    )
    appointment.updated_at = datetime.utcnow()
    queue_notification(
        db,
        appointment.customer.telegram_id,
        f"Сообщение от менеджера FLASHIN по заявке #{appointment.id}:\n{payload.body.strip()}",
    )
    _timeline(
        db,
        appointment,
        "showroom.manager_message",
        "Менеджер написал клиенту",
        {"admin_id": admin.id},
    )
    publish_showroom_event(
        db,
        appointment,
        "showroom.manager_message",
        {"admin_id": admin.id, "message_length": len(payload.body.strip())},
    )
    log_admin_action(
        db,
        admin,
        "showroom.message.send",
        "showroom_appointment",
        appointment.id,
        {"length": len(payload.body)},
    )
    db.commit()
    return _serialize(_load_admin(db, appointment.id))


@router.post("/admin/locations", response_model=ShowroomLocationOut)
def create_location(
    payload: ShowroomLocationCreate,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "appointments.manage_locations")
    location = ShowroomLocation(**payload.model_dump())
    db.add(location)
    db.flush()
    log_admin_action(
        db,
        admin,
        "showroom.location.create",
        "showroom_location",
        location.id,
        payload.model_dump(),
    )
    db.commit()
    db.refresh(location)
    return location


@router.get("/admin/product-profiles", response_model=list[ProductShowroomProfileOut])
def product_profiles(
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "appointments.read")
    return (
        db.query(ProductShowroomProfile)
        .order_by(ProductShowroomProfile.updated_at.desc())
        .all()
    )


@router.put("/admin/products/{product_id}/profile", response_model=ProductShowroomProfileOut)
def upsert_product_profile(
    product_id: int,
    payload: ProductShowroomProfileIn,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "appointments.write")
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    profile = (
        db.query(ProductShowroomProfile)
        .filter(ProductShowroomProfile.product_id == product_id)
        .first()
    )
    if not profile:
        profile = ProductShowroomProfile(product_id=product_id)
        db.add(profile)
    for key, value in payload.model_dump().items():
        setattr(profile, key, _utc_naive(value) if key == "expected_at" else value)
    profile.updated_at = datetime.utcnow()
    log_admin_action(
        db,
        admin,
        "showroom.product_profile.upsert",
        "product",
        product_id,
        payload.model_dump(),
    )
    db.commit()
    db.refresh(profile)
    return profile
