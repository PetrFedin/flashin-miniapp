from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import MarketingCampaign
from ..notification_statuses import normalize_notification_message
from ..schemas import CampaignScheduleIn, MarketingCampaignCreate, MarketingCampaignOut
from ..security import get_current_admin
from ..services.campaigns import queue_campaign
from ..services.rbac import require_permission

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


def _clean(value: object, field: str, maximum: int) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail=f"{field} is required")
    if len(normalized) > maximum:
        raise HTTPException(status_code=400, detail=f"{field} is too long")
    return normalized


@router.post("", response_model=MarketingCampaignOut)
def create_campaign(
    payload: MarketingCampaignCreate,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "support.write")
    campaign = MarketingCampaign(
        name=_clean(payload.name, "Campaign name", 255),
        segment=_clean(payload.segment, "Campaign segment", 120),
        message=normalize_notification_message(payload.message, truncate=True),
        status="draft",
        sent_count=0,
        scheduled_at=None,
        sent_at=None,
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return campaign


@router.post("/{campaign_id}/queue")
def queue(
    campaign_id: int,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "support.write")
    campaign = (
        db.query(MarketingCampaign)
        .filter(MarketingCampaign.id == campaign_id)
        .with_for_update()
        .first()
    )
    if not campaign:
        db.rollback()
        raise HTTPException(status_code=404, detail="Campaign not found")
    try:
        count = queue_campaign(db, campaign)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    return {"ok": True, "queued": count, "status": campaign.status}


@router.get("", response_model=list[MarketingCampaignOut])
def list_campaigns(
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "support.write")
    return (
        db.query(MarketingCampaign)
        .order_by(MarketingCampaign.created_at.desc(), MarketingCampaign.id.desc())
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
    require_permission(db, admin, "support.write")
    campaign = (
        db.query(MarketingCampaign)
        .filter(MarketingCampaign.id == campaign_id)
        .with_for_update()
        .first()
    )
    if not campaign:
        db.rollback()
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.status == "queued":
        db.rollback()
        raise HTTPException(status_code=409, detail="Queued campaign cannot be rescheduled")
    campaign.status = "scheduled"
    campaign.scheduled_at = payload.scheduled_at
    campaign.sent_count = 0
    campaign.sent_at = None
    db.commit()
    return {"ok": True, "status": campaign.status, "scheduled_at": campaign.scheduled_at}
