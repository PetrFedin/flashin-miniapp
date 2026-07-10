from datetime import datetime
from sqlalchemy.orm import Session
from ..models import CrmProfile, Customer, MarketingCampaign, Notification


def queue_campaign(db: Session, campaign: MarketingCampaign) -> int:
    query = db.query(Customer)
    if campaign.segment != "all":
        customer_ids = [p.customer_id for p in db.query(CrmProfile).filter(CrmProfile.segment == campaign.segment).all()]
        query = query.filter(Customer.id.in_(customer_ids))
    customers = query.all()
    sent = 0
    for c in customers:
        if not c.telegram_id:
            continue
        db.add(Notification(telegram_id=c.telegram_id, message=campaign.message, status="pending"))
        sent += 1
    campaign.status = "queued"
    campaign.sent_count = sent
    campaign.sent_at = datetime.utcnow()
    return sent
