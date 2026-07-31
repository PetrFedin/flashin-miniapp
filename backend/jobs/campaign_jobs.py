import logging
from datetime import datetime

from sqlalchemy.orm import Session

from ..models import MarketingCampaign
from ..services.campaigns import queue_campaign

logger = logging.getLogger(__name__)
_MAX_CAMPAIGNS_PER_RUN = 100


def queue_due_campaigns(db: Session) -> int:
    now = datetime.utcnow()
    due = (
        db.query(MarketingCampaign)
        .filter(
            MarketingCampaign.status == "scheduled",
            MarketingCampaign.scheduled_at.is_not(None),
            MarketingCampaign.scheduled_at <= now,
        )
        .order_by(MarketingCampaign.id.asc())
        .with_for_update(skip_locked=True)
        .limit(_MAX_CAMPAIGNS_PER_RUN)
        .all()
    )
    queued_campaigns = 0
    for campaign in due:
        try:
            with db.begin_nested():
                queue_campaign(db, campaign)
                db.flush()
            queued_campaigns += 1
        except Exception:
            logger.exception("Failed to queue marketing campaign id=%s", campaign.id)
            # A malformed scheduled campaign must not poison every later campaign
            # or be retried forever by each scheduler tick.
            campaign.status = "failed"
            campaign.sent_count = 0
            campaign.sent_at = now
    db.commit()
    return queued_campaigns
