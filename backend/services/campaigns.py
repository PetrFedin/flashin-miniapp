from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import CrmProfile, Customer, MarketingCampaign
from ..notification_statuses import normalize_notification_message
from .notifications import queue_notification

_ALLOWED_QUEUE_STATES = frozenset({"draft", "scheduled"})


def _campaign_message(campaign: MarketingCampaign) -> str:
    return normalize_notification_message(campaign.message, truncate=True)


def queue_campaign(db: Session, campaign: MarketingCampaign) -> int:
    if campaign.id is None:
        raise HTTPException(status_code=409, detail="Campaign must be persisted before queueing")
    normalized_status = str(campaign.status or "").strip().lower()
    if normalized_status == "queued":
        return int(campaign.sent_count or 0)
    if normalized_status not in _ALLOWED_QUEUE_STATES:
        raise HTTPException(status_code=409, detail="Campaign cannot be queued from its current state")

    message = _campaign_message(campaign)
    query = db.query(Customer)
    segment = str(campaign.segment or "all").strip()
    if segment != "all":
        query = query.join(
            CrmProfile,
            CrmProfile.customer_id == Customer.id,
        ).filter(CrmProfile.segment == segment)

    queued = 0
    for customer in query.order_by(Customer.id.asc()).yield_per(500):
        telegram_id = str(customer.telegram_id or "").strip()
        if not telegram_id:
            continue
        if queue_notification(
            db,
            telegram_id,
            message,
            deduplication_key=f"campaign:{campaign.id}:customer:{customer.id}",
        ):
            queued += 1

    campaign.status = "queued"
    campaign.sent_count = queued
    campaign.sent_at = datetime.utcnow()
    return queued
