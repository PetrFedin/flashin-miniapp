from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import CustomerTimelineEvent
from ..schemas import TimelineEventOut
from ..security import get_current_admin, get_current_customer
from ..services.rbac import require_permission

router = APIRouter(prefix="/timeline", tags=["timeline"])


@router.get("", response_model=list[TimelineEventOut])
def my_timeline(customer=Depends(get_current_customer), db: Session = Depends(get_db)):
    return db.query(CustomerTimelineEvent).filter(CustomerTimelineEvent.customer_id == customer.id).order_by(CustomerTimelineEvent.created_at.desc()).limit(100).all()


@router.get("/admin/customers/{customer_id}", response_model=list[TimelineEventOut])
def admin_customer_timeline(customer_id: int, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "customers.read")
    return db.query(CustomerTimelineEvent).filter(CustomerTimelineEvent.customer_id == customer_id).order_by(CustomerTimelineEvent.created_at.desc()).limit(200).all()
