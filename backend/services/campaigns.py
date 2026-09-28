from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import utcnow_naive
from ..models import ConsentRecord, CrmProfile, Customer, MarketingCampaign
from .notifications import NOTIFICATION_PURPOSE_MARKETING, queue_notification

_QUEUEABLE_CAMPAIGN_STATUSES = {"draft", "scheduled"}


@dataclass(frozen=True)
class CampaignQueueResult:
    queued: int
    changed: bool


def _marketing_recipients(db: Session, segment: str):
    latest_consent_ids = (
        db.query(func.max(ConsentRecord.id).label("consent_id"))
        .filter(ConsentRecord.consent_type == "marketing")
        .group_by(ConsentRecord.customer_id)
        .subquery()
    )
    consented_customers = (
        db.query(ConsentRecord.customer_id.label("customer_id"))
        .join(latest_consent_ids, ConsentRecord.id == latest_consent_ids.c.consent_id)
        .filter(ConsentRecord.granted.is_(True))
        .subquery()
    )

    query = db.query(Customer).join(
        consented_customers,
        consented_customers.c.customer_id == Customer.id,
    )
    if segment != "all":
        query = query.join(CrmProfile, CrmProfile.customer_id == Customer.id).filter(
            CrmProfile.segment == segment
        )
    return query.distinct().all()


def queue_campaign(db: Session, campaign: MarketingCampaign) -> CampaignQueueResult:
    locked = (
        db.query(MarketingCampaign)
        .filter(MarketingCampaign.id == campaign.id)
        .with_for_update()
        .populate_existing()
        .first()
    )
    if locked is None:
        raise ValueError("Campaign not found")
    if locked.status == "queued":
        return CampaignQueueResult(queued=0, changed=False)
    if locked.status not in _QUEUEABLE_CAMPAIGN_STATUSES:
        raise ValueError(f"Campaign in status {locked.status} cannot be queued")

    customers = _marketing_recipients(db, locked.segment)
    queued = 0
    for customer in customers:
        if not customer.telegram_id:
            continue
        if queue_notification(
            db,
            customer.telegram_id,
            locked.message,
            event_key=f"campaign:{locked.id}:customer:{customer.id}",
            purpose=NOTIFICATION_PURPOSE_MARKETING,
            customer_id=customer.id,
            campaign_id=locked.id,
        ):
            queued += 1

    locked.status = "queued"
    locked.sent_count = queued
    locked.sent_at = utcnow_naive()
    return CampaignQueueResult(queued=queued, changed=True)
