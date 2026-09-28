from sqlalchemy.orm import Session

from ..database import utcnow_naive
from ..models import MarketingCampaign
from ..services.campaigns import queue_campaign


def queue_due_campaigns(db: Session) -> int:
    due = (
        db.query(MarketingCampaign)
        .filter(
            MarketingCampaign.status == "scheduled",
            MarketingCampaign.scheduled_at <= utcnow_naive(),
        )
        .all()
    )
    changed = 0
    for campaign in due:
        result = queue_campaign(db, campaign)
        if result.changed:
            changed += 1
    db.commit()
    return changed
