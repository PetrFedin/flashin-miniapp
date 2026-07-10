from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import MarketingCampaign
from ..schemas import MarketingCampaignCreate, MarketingCampaignOut, CampaignScheduleIn
from ..security import get_current_admin
from ..services.campaigns import queue_campaign
from ..services.rbac import require_permission

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


@router.post("", response_model=MarketingCampaignOut)
def create_campaign(payload: MarketingCampaignCreate, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "support.write")
    campaign = MarketingCampaign(name=payload.name, segment=payload.segment, message=payload.message, status="draft")
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return campaign


@router.post("/{campaign_id}/queue")
def queue(campaign_id: int, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "support.write")
    campaign = db.query(MarketingCampaign).filter(MarketingCampaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    count = queue_campaign(db, campaign)
    db.commit()
    return {"ok": True, "queued": count}


@router.get("", response_model=list[MarketingCampaignOut])
def list_campaigns(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "support.write")
    return db.query(MarketingCampaign).order_by(MarketingCampaign.created_at.desc()).limit(100).all()



@router.post("/{campaign_id}/schedule")
def schedule_campaign(campaign_id: int, payload: CampaignScheduleIn, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "support.write")
    campaign = db.query(MarketingCampaign).filter(MarketingCampaign.id == campaign_id).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    campaign.status = "scheduled"
    campaign.scheduled_at = payload.scheduled_at
    db.commit()
    return {"ok": True}
