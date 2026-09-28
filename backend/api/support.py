from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db, utcnow_naive
from ..models import AdminUser, Customer, Order, SupportTicket
from ..schemas import SupportTicketCreate, SupportTicketOut, SupportTicketUpdate
from ..security import get_current_admin, get_current_customer
from ..services.rbac import require_permission

router = APIRouter(prefix="/support", tags=["support"])

_TICKET_TRANSITIONS = {
    "open": {"in_progress", "waiting_customer", "resolved", "closed"},
    "in_progress": {"waiting_customer", "resolved", "closed"},
    "waiting_customer": {"in_progress", "resolved", "closed"},
    "resolved": {"in_progress", "closed"},
    "closed": set(),
}
_PRIORITIES = {"low", "normal", "high", "urgent"}


class AdminSupportTicketOut(SupportTicketOut):
    assigned_admin_id: int | None = None


def _clean_text(value: str, field: str, minimum: int, maximum: int) -> str:
    cleaned = (value or "").strip()
    if len(cleaned) < minimum:
        raise HTTPException(status_code=400, detail=f"{field} is too short")
    if len(cleaned) > maximum:
        raise HTTPException(status_code=400, detail=f"{field} is too long")
    return cleaned


@router.post("/tickets", response_model=SupportTicketOut)
def create_ticket(
    payload: SupportTicketCreate,
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    priority = (payload.priority or "normal").strip().lower()
    if priority not in _PRIORITIES:
        raise HTTPException(status_code=400, detail="Unsupported ticket priority")

    if payload.order_id is not None:
        order = (
            db.query(Order)
            .filter(
                Order.id == payload.order_id,
                Order.customer_id == customer.id,
            )
            .first()
        )
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")

    ticket = SupportTicket(
        customer_id=customer.id,
        order_id=payload.order_id,
        subject=_clean_text(payload.subject, "Subject", 3, 255),
        message=_clean_text(payload.message, "Message", 5, 5000),
        priority=priority,
        status="open",
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket


@router.get("/tickets", response_model=list[SupportTicketOut])
def my_tickets(
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    return (
        db.query(SupportTicket)
        .filter(SupportTicket.customer_id == customer.id)
        .order_by(SupportTicket.created_at.desc())
        .all()
    )


@router.get("/admin/tickets", response_model=list[AdminSupportTicketOut])
def admin_tickets(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "support.write")
    return db.query(SupportTicket).order_by(SupportTicket.created_at.desc()).all()


@router.patch("/admin/tickets/{ticket_id}", response_model=AdminSupportTicketOut)
def admin_update_ticket(
    ticket_id: int,
    payload: SupportTicketUpdate,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "support.write")
    try:
        ticket = (
            db.query(SupportTicket)
            .filter(SupportTicket.id == ticket_id)
            .with_for_update()
            .first()
        )
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")

        if payload.status:
            next_status = payload.status.strip().lower()
            if next_status not in _TICKET_TRANSITIONS:
                raise HTTPException(status_code=400, detail="Unsupported ticket status")
            if next_status != ticket.status and next_status not in _TICKET_TRANSITIONS.get(ticket.status, set()):
                raise HTTPException(
                    status_code=409,
                    detail=f"Ticket transition {ticket.status} -> {next_status} is not allowed",
                )
            ticket.status = next_status

        if payload.priority:
            priority = payload.priority.strip().lower()
            if priority not in _PRIORITIES:
                raise HTTPException(status_code=400, detail="Unsupported ticket priority")
            ticket.priority = priority

        if payload.assigned_admin_id is not None:
            assignee = (
                db.query(AdminUser)
                .filter(
                    AdminUser.id == payload.assigned_admin_id,
                    AdminUser.active.is_(True),
                )
                .first()
            )
            if not assignee:
                raise HTTPException(status_code=409, detail="Assigned administrator not found or inactive")
            ticket.assigned_admin_id = assignee.id

        ticket.updated_at = utcnow_naive()
        db.commit()
        db.refresh(ticket)
        return ticket
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
