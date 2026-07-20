from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import WebhookOutbox
from ..schemas import WebhookOutboxOut
from ..security import get_current_admin
from ..services.rbac import require_permission

router = APIRouter(prefix="/outbox", tags=["outbox"])


@router.get("", response_model=list[WebhookOutboxOut])
def list_outbox(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "webhooks.read")
    return db.query(WebhookOutbox).order_by(WebhookOutbox.created_at.desc()).limit(200).all()


@router.post("/{row_id}/retry")
def retry_outbox(row_id: int, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "webhooks.write")
    row = db.query(WebhookOutbox).filter(WebhookOutbox.id == row_id).first()
    if row:
        row.status = "pending"
        row.last_error = ""
        db.commit()
    return {"ok": True}
