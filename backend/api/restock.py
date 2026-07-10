from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import Customer, RestockSubscription
from ..schemas import RestockSubscribeIn
from ..security import get_current_customer

router = APIRouter(prefix="/restock", tags=["restock"])


@router.post("/subscribe")
def subscribe(payload: RestockSubscribeIn, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    existing = db.query(RestockSubscription).filter(RestockSubscription.customer_id == customer.id, RestockSubscription.variant_id == payload.variant_id).first()
    if existing:
        existing.active = True
    else:
        db.add(RestockSubscription(customer_id=customer.id, variant_id=payload.variant_id, active=True))
    db.commit()
    return {"ok": True}
