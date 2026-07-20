from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import Customer, SupportTicket
from ..schemas import SupportTicketCreate, SupportTicketOut, SupportTicketUpdate
from ..security import get_current_admin, get_current_customer
from ..services.rbac import require_permission

router = APIRouter(prefix="/support", tags=["support"])


@router.post("/tickets", response_model=SupportTicketOut)
def create_ticket(payload: SupportTicketCreate, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    ticket = SupportTicket(
        customer_id=customer.id,
        order_id=payload.order_id,
        subject=payload.subject,
        message=payload.message,
        priority=payload.priority,
        status="open",
    )
    db.add(ticket)
    db.commit()
    db.refresh(ticket)
    return ticket


@router.get("/tickets", response_model=list[SupportTicketOut])
def my_tickets(customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    return db.query(SupportTicket).filter(SupportTicket.customer_id == customer.id).order_by(SupportTicket.created_at.desc()).all()


@router.get("/admin/tickets", response_model=list[SupportTicketOut])
def admin_tickets(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "support.read")
    return db.query(SupportTicket).order_by(SupportTicket.created_at.desc()).all()


@router.patch("/admin/tickets/{ticket_id}", response_model=SupportTicketOut)
def admin_update_ticket(ticket_id: int, payload: SupportTicketUpdate, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "support.write")
    ticket = db.query(SupportTicket).filter(SupportTicket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if payload.status:
        ticket.status = payload.status
    if payload.priority:
        ticket.priority = payload.priority
    if payload.assigned_admin_id is not None:
        ticket.assigned_admin_id = payload.assigned_admin_id
    db.commit()
    return ticket
