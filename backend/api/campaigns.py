from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import MarketingCampaign
from ..schemas import CampaignScheduleIn, MarketingCampaignCreate, MarketingCampaignOut
from ..security import get_current_admin
from ..services.audit import log_admin_action
from ..services.campaigns import queue_campaign
from ..services.rbac import (
    CAMPAIGNS_READ_PERMISSION,
    CAMPAIGNS_SEND_PERMISSION,
    CAMPAIGNS_WRITE_PERMISSION,
    require_permission,
)

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


def _campaign_create_values(payload: MarketingCampaignCreate) -> tuple[str, str, str]:
    name = (payload.name or "").strip()
    segment = (payload.segment or "all").strip()
    message = (payload.message or "").strip()
    if not name or len(name) > 255:
        raise HTTPException(status_code=400, detail="Campaign name is required and must be at most 255 characters")
    if not segment or len(segment) > 120:
        raise HTTPException(status_code=400, detail="Campaign segment is required and must be at most 120 characters")
    if not message or len(message) > 4096:
        raise HTTPException(status_code=400, detail="Campaign message is required and must be at most 4096 characters")
    return name, segment, message


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


@router.post("", response_model=MarketingCampaignOut)
def create_campaign(
    payload: MarketingCampaignCreate,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, CAMPAIGNS_WRITE_PERMISSION)
    name, segment, message = _campaign_create_values(payload)
    try:
        campaign = MarketingCampaign(
            name=name,
            segment=segment,
            message=message,
            status="draft",
        )
        db.add(campaign)
        db.flush()
        log_admin_action(
            db,
            admin,
            "campaign.create",
            "marketing_campaign",
            campaign.id,
            {"name": name, "segment": segment},
        )
        db.commit()
        db.refresh(campaign)
        return campaign
    except Exception:
        db.rollback()
        raise


@router.post("/{campaign_id}/queue")
def queue(
    campaign_id: int,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, CAMPAIGNS_SEND_PERMISSION)
    campaign = db.query(MarketingCampaign).filter(MarketingCampaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    try:
        result = queue_campaign(db, campaign)
        if result.changed:
            log_admin_action(
                db,
                admin,
                "campaign.queue",
                "marketing_campaign",
                campaign.id,
                {"segment": campaign.segment, "queued": result.queued},
            )
        db.commit()
        return {
            "ok": True,
            "queued": result.queued,
            "idempotent": not result.changed,
        }
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise


@router.get("", response_model=list[MarketingCampaignOut])
def list_campaigns(
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, CAMPAIGNS_READ_PERMISSION)
    return (
        db.query(MarketingCampaign)
        .order_by(MarketingCampaign.created_at.desc())
        .limit(100)
        .all()
    )


@router.post("/{campaign_id}/schedule")
def schedule_campaign(
    campaign_id: int,
    payload: CampaignScheduleIn,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, CAMPAIGNS_SEND_PERMISSION)
    try:
        campaign = (
            db.query(MarketingCampaign)
            .filter(MarketingCampaign.id == campaign_id)
            .with_for_update()
            .first()
        )
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        if campaign.status == "queued":
            raise HTTPException(status_code=409, detail="Queued campaign cannot be rescheduled")
        if campaign.status not in {"draft", "scheduled"}:
            raise HTTPException(status_code=409, detail=f"Campaign in status {campaign.status} cannot be scheduled")

        scheduled_at = _utc_naive(payload.scheduled_at)
        if campaign.status == "scheduled" and campaign.scheduled_at == scheduled_at:
            db.commit()
            return {"ok": True, "idempotent": True}

        previous_status = campaign.status
        campaign.status = "scheduled"
        campaign.scheduled_at = scheduled_at
        log_admin_action(
            db,
            admin,
            "campaign.schedule",
            "marketing_campaign",
            campaign.id,
            {
                "previous_status": previous_status,
                "scheduled_at": scheduled_at.isoformat() + "Z",
                "segment": campaign.segment,
            },
        )
        db.commit()
        return {"ok": True, "idempotent": False}
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
