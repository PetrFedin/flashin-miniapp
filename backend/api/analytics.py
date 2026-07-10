import json
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import AnalyticsEvent, Customer
from ..schemas import AnalyticsEventIn
from ..security import get_current_customer

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.post("/events")
def track_event(payload: AnalyticsEventIn, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    db.add(AnalyticsEvent(customer_id=customer.id, event_type=payload.event_type, payload=json.dumps(payload.payload, ensure_ascii=False)))
    db.commit()
    return {"ok": True}
