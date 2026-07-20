from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import WebhookDestination
from ..schemas import WebhookDestinationCreate, WebhookDestinationOut
from ..security import get_current_admin
from ..services.rbac import require_permission

router = APIRouter(prefix="/webhook-destinations", tags=["webhook-destinations"])


@router.get("", response_model=list[WebhookDestinationOut])
def list_destinations(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "webhooks.read")
    return db.query(WebhookDestination).order_by(WebhookDestination.created_at.desc()).all()


@router.post("", response_model=WebhookDestinationOut)
def create_destination(payload: WebhookDestinationCreate, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "webhooks.write")
    row = WebhookDestination(**payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
