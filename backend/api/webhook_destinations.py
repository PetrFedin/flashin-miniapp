import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import WebhookDestination
from ..schemas import WebhookDestinationCreate, WebhookDestinationOut
from ..security import get_current_admin
from ..services.audit import log_admin_action
from ..services.rbac import WEBHOOKS_CONFIGURE_PERMISSION, require_permission
from ..services.webhook_security import normalize_webhook_url, redact_webhook_url

router = APIRouter(prefix="/webhook-destinations", tags=["webhook-destinations"])

_EVENT_TYPE_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


def _normalize_event_type(value: str) -> str:
    event_type = (value or "*").strip()
    if event_type == "*":
        return event_type
    if len(event_type) > 120 or not _EVENT_TYPE_RE.fullmatch(event_type):
        raise HTTPException(status_code=400, detail="Invalid webhook event type")
    return event_type


def _public_destination(row: WebhookDestination) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "url": redact_webhook_url(row.url),
        "event_type": row.event_type,
        "active": row.active,
    }


@router.get("", response_model=list[WebhookDestinationOut])
def list_destinations(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "webhooks.read")
    rows = db.query(WebhookDestination).order_by(WebhookDestination.created_at.desc()).all()
    return [_public_destination(row) for row in rows]


@router.post("", response_model=WebhookDestinationOut)
def create_destination(
    payload: WebhookDestinationCreate,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, WEBHOOKS_CONFIGURE_PERMISSION)
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Webhook destination name is required")
    if len(name) > 255:
        raise HTTPException(status_code=400, detail="Webhook destination name is too long")

    try:
        url = normalize_webhook_url(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    event_type = _normalize_event_type(payload.event_type)
    signing_secret = (payload.signing_secret or "").strip()
    if signing_secret and len(signing_secret) < 32:
        raise HTTPException(status_code=400, detail="Webhook signing secret must contain at least 32 characters")
    if len(signing_secret) > 255:
        raise HTTPException(status_code=400, detail="Webhook signing secret is too long")

    try:
        existing = (
            db.query(WebhookDestination)
            .filter(WebhookDestination.url == url, WebhookDestination.event_type == event_type)
            .first()
        )
        if existing:
            raise HTTPException(status_code=409, detail="Webhook destination already exists for this event")

        row = WebhookDestination(
            name=name,
            url=url,
            event_type=event_type,
            active=payload.active,
            signing_secret=signing_secret,
        )
        db.add(row)
        db.flush()
        log_admin_action(
            db,
            admin,
            "webhook_destination.create",
            "webhook_destination",
            row.id,
            {
                "name": name,
                "url": redact_webhook_url(url),
                "event_type": event_type,
                "active": payload.active,
            },
        )
        db.commit()
        db.refresh(row)
        return _public_destination(row)
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Webhook destination already exists") from exc
    except Exception:
        db.rollback()
        raise


@router.patch("/{destination_id}/active", response_model=WebhookDestinationOut)
def set_destination_active(
    destination_id: int,
    active: bool,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, WEBHOOKS_CONFIGURE_PERMISSION)
    try:
        row = (
            db.query(WebhookDestination)
            .filter(WebhookDestination.id == destination_id)
            .with_for_update()
            .first()
        )
        if not row:
            raise HTTPException(status_code=404, detail="Webhook destination not found")
        previous = row.active
        row.active = active
        log_admin_action(
            db,
            admin,
            "webhook_destination.active",
            "webhook_destination",
            row.id,
            {"previous": previous, "active": active},
        )
        db.commit()
        db.refresh(row)
        return _public_destination(row)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
