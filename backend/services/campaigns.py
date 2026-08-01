from sqlalchemy.orm import Session

from ..database import utcnow_naive
from ..models import CrmProfile, Customer, MarketingCampaign, Notification


def queue_campaign(db: Session, campaign: MarketingCampaign) -> int:
    query = db.query(Customer)
    if campaign.segment != "all":
        customer_ids = [
            profile.customer_id
            for profile in db.query(CrmProfile)
            .filter(CrmProfile.segment == campaign.segment)
            .all()
        ]
        query = query.filter(Customer.id.in_(customer_ids))
    customers = query.all()
    sent = 0
    for customer in customers:
        if not customer.telegram_id:
            continue
        db.add(
            Notification(
                telegram_id=customer.telegram_id,
                message=campaign.message,
                status="pending",
            )
        )
        sent += 1
    campaign.status = "queued"
    campaign.sent_count = sent
    campaign.sent_at = utcnow_naive()
    return sent
