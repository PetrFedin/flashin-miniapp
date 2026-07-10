from datetime import datetime
from sqlalchemy.orm import Session
from ..models import MarketingCampaign
from ..services.campaigns import queue_campaign


def queue_due_campaigns(db: Session) -> int:
    due = (
        db.query(MarketingCampaign)
        .filter(MarketingCampaign.status == "scheduled", MarketingCampaign.scheduled_at <= datetime.utcnow())
        .all()
    )
    count = 0
    for campaign in due:
        queue_campaign(db, campaign)
        count += 1
    db.commit()
    return count
