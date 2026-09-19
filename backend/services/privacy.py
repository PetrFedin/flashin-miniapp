from uuid import uuid4

from sqlalchemy.orm import Session

from ..database import utcnow_naive
from .privacy_export import build_customer_export
from ..models import (
    Cart,
    ConsentRecord,
    Customer,
    CustomerTimelineEvent,
    Notification,
    Order,
    PrivacyRequest,
    RestockSubscription,
    ReturnRequest,
    SupportTicket,
    WishlistItem,
)

OPTIONAL_CONSENT_TYPES = {"marketing", "analytics", "personalization"}
ALLOWED_CONSENT_TYPES = OPTIONAL_CONSENT_TYPES | {"privacy", "terms"}
OPEN_PRIVACY_REQUEST_STATUSES = {"requested", "processing"}


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def withdraw_optional_consents(db: Session, customer_id: int, source: str = "privacy_request") -> int:
    count = 0
    for consent_type in sorted(OPTIONAL_CONSENT_TYPES):
        latest = (
            db.query(ConsentRecord)
            .filter(
                ConsentRecord.customer_id == customer_id,
                ConsentRecord.consent_type == consent_type,
            )
            .order_by(ConsentRecord.created_at.desc(), ConsentRecord.id.desc())
            .with_for_update()
            .first()
        )
        if latest and not latest.granted:
            continue
        db.add(
            ConsentRecord(
                customer_id=customer_id,
                consent_type=consent_type,
                granted=False,
                source=source,
            )
        )
        count += 1
    return count


def anonymize_customer(db: Session, customer: Customer) -> dict:
    original_telegram_id = customer.telegram_id
    orders = db.query(Order).filter(Order.customer_id == customer.id).with_for_update().all()
    for order in orders:
        order.address = ""
        order.comment = ""
        order.referral_code = ""

    return_requests = (
        db.query(ReturnRequest)
        .filter(ReturnRequest.customer_id == customer.id)
        .with_for_update()
        .all()
    )
    for request in return_requests:
        request.reason = "[redacted after privacy deletion]"

    tickets = (
        db.query(SupportTicket)
        .filter(SupportTicket.customer_id == customer.id)
        .with_for_update()
        .all()
    )
    for ticket in tickets:
        ticket.customer_id = None
        ticket.subject = "[redacted]"
        ticket.message = "[redacted after privacy deletion]"

    db.query(WishlistItem).filter(WishlistItem.customer_id == customer.id).delete(
        synchronize_session=False
    )
    db.query(RestockSubscription).filter(
        RestockSubscription.customer_id == customer.id
    ).delete(synchronize_session=False)
    db.query(CustomerTimelineEvent).filter(
        CustomerTimelineEvent.customer_id == customer.id
    ).delete(synchronize_session=False)
    db.query(Notification).filter(Notification.telegram_id == original_telegram_id).delete(
        synchronize_session=False
    )

    carts = db.query(Cart).filter(Cart.customer_id == customer.id).with_for_update().all()
    removed_carts = 0
    for cart in carts:
        if cart.status != "converted":
            db.delete(cart)
            removed_carts += 1

    referrals = (
        db.query(ReferralCode)
        .filter(ReferralCode.customer_id == customer.id)
        .with_for_update()
        .all()
    )
    for referral in referrals:
        referral.active = False

    profile = (
        db.query(CrmProfile)
        .filter(CrmProfile.customer_id == customer.id)
        .with_for_update()
        .first()
    )
    if profile:
        profile.segment = "deleted"
        profile.loyalty_points = 0
        profile.vip = False

    withdrawn = withdraw_optional_consents(db, customer.id, source="privacy_deletion")
    customer.telegram_id = f"deleted:{customer.id}:{uuid4().hex[:16]}"
    customer.username = ""
    customer.first_name = ""
    customer.last_name = ""
    customer.phone = ""
    customer.email = ""

    return {
        "orders_anonymized": len(orders),
        "returns_redacted": len(return_requests),
        "tickets_redacted": len(tickets),
        "carts_removed": removed_carts,
        "consents_withdrawn": withdrawn,
    }


def mark_privacy_processed(req: PrivacyRequest, result_url: str = "") -> None:
    req.status = "processed"
    req.result_url = result_url[:2048]
    req.processed_at = utcnow_naive()
