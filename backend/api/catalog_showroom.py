from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..catalog_models import ProductMerchandising, ShowroomAppointment
from ..database import get_db, utcnow_naive
from ..models import Customer, Product
from ..security import get_current_admin, get_current_customer
from ..services.audit import log_admin_action
from ..services.rbac import require_permission

router = APIRouter(prefix="/catalog", tags=["catalog-showroom"])


class ShowroomAppointmentCreate(BaseModel):
    product_id: int = Field(gt=0)
    starts_at: datetime
    duration_minutes: Literal[30] = 30
    notes: str = Field(default="", max_length=2000)


class ShowroomAppointmentStatus(BaseModel):
    status: Literal["requested", "confirmed", "cancelled", "completed"]


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(
            status_code=400,
            detail="Showroom appointment starts_at must include a timezone offset",
        )
    return value.astimezone(UTC).replace(tzinfo=None)


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def _appointment_payload(row: ShowroomAppointment, *, include_customer: bool) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": int(row.id),
        "product_id": int(row.product_id),
        "starts_at": _utc_iso(row.starts_at),
        "duration_minutes": int(row.duration_minutes),
        "status": str(row.status),
        "notes": str(row.notes or ""),
    }
    if include_customer:
        payload["customer_id"] = int(row.customer_id)
    return payload


@router.post("/showroom/appointments")
def create_showroom_appointment(
    payload: ShowroomAppointmentCreate,
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    product = (
        db.query(Product)
        .filter(Product.id == payload.product_id, Product.active.is_(True))
        .first()
    )
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    merchandising = (
        db.query(ProductMerchandising)
        .filter(ProductMerchandising.product_id == product.id)
        .first()
    )
    if merchandising and not merchandising.showroom_fitting_enabled:
        raise HTTPException(
            status_code=409,
            detail="Showroom fitting is disabled for this product",
        )

    starts_at = _utc_naive(payload.starts_at)
    now = utcnow_naive()
    if starts_at <= now:
        raise HTTPException(status_code=400, detail="Showroom appointment must be in the future")
    if starts_at > now + timedelta(days=90):
        raise HTTPException(
            status_code=400,
            detail="Showroom appointment cannot be more than 90 days ahead",
        )
    if starts_at.minute not in {0, 30} or starts_at.second or starts_at.microsecond:
        raise HTTPException(
            status_code=400,
            detail="Showroom appointment must start on a 30-minute boundary",
        )

    # Pilot appointments are fixed at 30 minutes. With boundary-aligned starts,
    # a unique UTC start slot also prevents any overlap between active bookings.
    slot_key = starts_at.strftime("%Y%m%d%H%M")
    row = ShowroomAppointment(
        customer_id=customer.id,
        product_id=product.id,
        starts_at=starts_at,
        duration_minutes=30,
        status="requested",
        notes=payload.notes.strip(),
        active_slot_key=slot_key,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="This showroom time is already reserved",
        ) from exc
    db.refresh(row)
    return _appointment_payload(row, include_customer=False)


@router.get("/showroom/appointments/me")
def my_showroom_appointments(
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(ShowroomAppointment)
        .filter(ShowroomAppointment.customer_id == customer.id)
        .order_by(ShowroomAppointment.starts_at.desc(), ShowroomAppointment.id.desc())
        .limit(100)
        .all()
    )
    return [_appointment_payload(row, include_customer=False) for row in rows]


@router.get("/admin/showroom/appointments")
def admin_showroom_appointments(
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "showroom.read")
    rows = (
        db.query(ShowroomAppointment)
        .order_by(ShowroomAppointment.starts_at.asc(), ShowroomAppointment.id.asc())
        .limit(500)
        .all()
    )
    return [_appointment_payload(row, include_customer=True) for row in rows]


@router.patch("/admin/showroom/appointments/{appointment_id}")
def admin_update_showroom_appointment(
    appointment_id: int,
    payload: ShowroomAppointmentStatus,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "showroom.write")
    row = (
        db.query(ShowroomAppointment)
        .filter(ShowroomAppointment.id == appointment_id)
        .with_for_update()
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Appointment not found")

    current = str(row.status)
    allowed = {
        "requested": {"confirmed", "cancelled"},
        "confirmed": {"completed", "cancelled"},
        "cancelled": set(),
        "completed": set(),
    }
    if payload.status == current:
        return {"ok": True, "id": row.id, "status": row.status}
    if payload.status not in allowed.get(current, set()):
        raise HTTPException(
            status_code=409,
            detail=f"Invalid showroom status transition: {current} -> {payload.status}",
        )

    row.status = payload.status
    if row.status in {"cancelled", "completed"}:
        row.active_slot_key = None
    else:
        row.active_slot_key = row.starts_at.strftime("%Y%m%d%H%M")
    row.updated_at = utcnow_naive()
    log_admin_action(
        db,
        admin,
        "showroom.appointment.update",
        "showroom_appointment",
        row.id,
        {"status": row.status},
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Showroom slot is no longer available",
        ) from exc
    return {"ok": True, "id": row.id, "status": row.status}
