from uuid import uuid4

from sqlalchemy.orm import Session

from ..database import utcnow_naive
from ..models import (
    Cart,
    ConsentRecord,
    CrmProfile,
    Customer,
    CustomerTimelineEvent,
    LoyaltyTransaction,
    Notification,
    Order,
    PrivacyRequest,
    ReferralCode,
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


def build_customer_export(db: Session, customer: Customer) -> dict:
    orders = (
        db.query(Order)
        .filter(Order.customer_id == customer.id)
        .order_by(Order.created_at.asc())
        .all()
    )
    carts = (
        db.query(Cart)
        .filter(Cart.customer_id == customer.id)
        .order_by(Cart.created_at.asc())
        .all()
    )
    wishlist = db.query(WishlistItem).filter(WishlistItem.customer_id == customer.id).all()
    restock = db.query(RestockSubscription).filter(RestockSubscription.customer_id == customer.id).all()
    consents = (
        db.query(ConsentRecord)
        .filter(ConsentRecord.customer_id == customer.id)
        .order_by(ConsentRecord.created_at.asc())
        .all()
    )
    requests = (
        db.query(PrivacyRequest)
        .filter(PrivacyRequest.customer_id == customer.id)
        .order_by(PrivacyRequest.created_at.asc())
        .all()
    )
    loyalty = (
        db.query(LoyaltyTransaction)
        .filter(LoyaltyTransaction.customer_id == customer.id)
        .order_by(LoyaltyTransaction.created_at.asc())
        .all()
    )
    profile = db.query(CrmProfile).filter(CrmProfile.customer_id == customer.id).first()
    referrals = db.query(ReferralCode).filter(ReferralCode.customer_id == customer.id).all()
    tickets = (
        db.query(SupportTicket)
        .filter(SupportTicket.customer_id == customer.id)
        .order_by(SupportTicket.created_at.asc())
        .all()
    )
    timeline = (
        db.query(CustomerTimelineEvent)
        .filter(CustomerTimelineEvent.customer_id == customer.id)
        .order_by(CustomerTimelineEvent.created_at.asc())
        .all()
    )

    return {
        "generated_at": utcnow_naive().isoformat() + "Z",
        "customer": {
            "id": customer.id,
            "telegram_id": customer.telegram_id,
            "username": customer.username,
            "first_name": customer.first_name,
            "last_name": customer.last_name,
            "phone": customer.phone,
            "email": customer.email,
            "created_at": _iso(customer.created_at),
        },
        "orders": [
            {
                "id": order.id,
                "status": order.status,
                "payment_status": order.payment_status,
                "delivery_status": order.delivery_status,
                "total_amount": order.total_amount,
                "delivery_price": order.delivery_price,
                "discount_amount": order.discount_amount,
                "loyalty_points_redeemed": order.loyalty_points_redeemed,
                "currency": order.currency,
                "delivery_type": order.delivery_type,
                "address": order.address,
                "comment": order.comment,
                "tracking_number": order.tracking_number,
                "created_at": _iso(order.created_at),
                "items": [
                    {
                        "product_id": item.product_id,
                        "variant_id": item.variant_id,
                        "title": item.title,
                        "size": item.size,
                        "quantity": item.quantity,
                        "price": item.price,
                    }
                    for item in order.items
                ],
                "payments": [
                    {
                        "provider": payment.provider,
                        "provider_payment_id": payment.provider_payment_id,
                        "status": payment.status,
                        "amount": payment.amount,
                        "created_at": _iso(payment.created_at),
                    }
                    for payment in order.payments
                ],
                "returns": [
                    {
                        "id": return_request.id,
                        "reason": return_request.reason,
                        "status": return_request.status,
                        "refund_amount": return_request.refund_amount,
                        "created_at": _iso(return_request.created_at),
                    }
                    for return_request in order.returns
                ],
            }
            for order in orders
        ],
        "carts": [
            {
                "id": cart.id,
                "status": cart.status,
                "referral_code": cart.referral_code,
                "loyalty_points_to_redeem": cart.loyalty_points_to_redeem,
                "created_at": _iso(cart.created_at),
                "items": [
                    {
                        "product_id": item.product_id,
                        "variant_id": item.variant_id,
                        "quantity": item.quantity,
                    }
                    for item in cart.items
                ],
            }
            for cart in carts
        ],
        "wishlist": [
            {"product_id": row.product_id, "created_at": _iso(row.created_at)}
            for row in wishlist
        ],
        "restock_subscriptions": [
            {
                "variant_id": row.variant_id,
                "active": row.active,
                "created_at": _iso(row.created_at),
            }
            for row in restock
        ],
        "consents": [
            {
                "type": row.consent_type,
                "granted": row.granted,
                "source": row.source,
                "created_at": _iso(row.created_at),
            }
            for row in consents
        ],
        "privacy_requests": [
            {
                "id": row.id,
                "type": row.request_type,
                "status": row.status,
                "created_at": _iso(row.created_at),
                "processed_at": _iso(row.processed_at),
            }
            for row in requests
        ],
        "loyalty": {
            "profile": (
                {
                    "segment": profile.segment,
                    "orders_count": profile.orders_count,
                    "total_spent": profile.total_spent,
                    "average_order_value": profile.average_order_value,
                    "loyalty_points": profile.loyalty_points,
                    "vip": profile.vip,
                }
                if profile
                else None
            ),
            "transactions": [
                {
                    "order_id": row.order_id,
                    "points_delta": row.points_delta,
                    "reason": row.reason,
                    "created_at": _iso(row.created_at),
                }
                for row in loyalty
            ],
        },
        "referral_codes": [
            {
                "code": row.code,
                "reward_points": row.reward_points,
                "used_count": row.used_count,
                "active": row.active,
                "created_at": _iso(row.created_at),
            }
            for row in referrals
        ],
        "support_tickets": [
            {
                "id": row.id,
                "order_id": row.order_id,
                "subject": row.subject,
                "message": row.message,
                "status": row.status,
                "priority": row.priority,
                "created_at": _iso(row.created_at),
            }
            for row in tickets
        ],
        "timeline": [
            {
                "event_type": row.event_type,
                "title": row.title,
                "payload": row.payload,
                "created_at": _iso(row.created_at),
            }
            for row in timeline
        ],
    }


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
