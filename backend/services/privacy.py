import json
import math
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import uuid4

from sqlalchemy.orm import Session

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
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        else:
            value = value.astimezone(UTC)
        return value.isoformat(timespec="seconds").replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    raise ValueError("Privacy export contains an invalid date value")


def _money_text(value: object) -> str:
    try:
        amount = Decimal(str(value or 0)).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Privacy export contains an invalid monetary value") from exc
    if not amount.is_finite():
        raise ValueError("Privacy export contains a non-finite monetary value")
    return format(amount, ".2f")


def _json_safe(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Privacy export contains a non-finite decimal")
        return format(value, "f")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Privacy export contains a non-finite number")
        return value
    if isinstance(value, (datetime, date)):
        return _iso(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def render_customer_export(data: dict) -> str:
    return json.dumps(
        _json_safe(data),
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
        sort_keys=True,
    )


def build_customer_export(db: Session, customer: Customer) -> dict:
    orders = (
        db.query(Order)
        .filter(Order.customer_id == customer.id)
        .order_by(Order.created_at.asc(), Order.id.asc())
        .all()
    )
    carts = (
        db.query(Cart)
        .filter(Cart.customer_id == customer.id)
        .order_by(Cart.created_at.asc(), Cart.id.asc())
        .all()
    )
    wishlist = (
        db.query(WishlistItem)
        .filter(WishlistItem.customer_id == customer.id)
        .order_by(WishlistItem.created_at.asc(), WishlistItem.id.asc())
        .all()
    )
    restock = (
        db.query(RestockSubscription)
        .filter(RestockSubscription.customer_id == customer.id)
        .order_by(RestockSubscription.created_at.asc(), RestockSubscription.id.asc())
        .all()
    )
    consents = (
        db.query(ConsentRecord)
        .filter(ConsentRecord.customer_id == customer.id)
        .order_by(ConsentRecord.created_at.asc(), ConsentRecord.id.asc())
        .all()
    )
    requests = (
        db.query(PrivacyRequest)
        .filter(PrivacyRequest.customer_id == customer.id)
        .order_by(PrivacyRequest.created_at.asc(), PrivacyRequest.id.asc())
        .all()
    )
    loyalty = (
        db.query(LoyaltyTransaction)
        .filter(LoyaltyTransaction.customer_id == customer.id)
        .order_by(LoyaltyTransaction.created_at.asc(), LoyaltyTransaction.id.asc())
        .all()
    )
    profile = db.query(CrmProfile).filter(CrmProfile.customer_id == customer.id).first()
    referrals = (
        db.query(ReferralCode)
        .filter(ReferralCode.customer_id == customer.id)
        .order_by(ReferralCode.created_at.asc(), ReferralCode.id.asc())
        .all()
    )
    tickets = (
        db.query(SupportTicket)
        .filter(SupportTicket.customer_id == customer.id)
        .order_by(SupportTicket.created_at.asc(), SupportTicket.id.asc())
        .all()
    )
    timeline = (
        db.query(CustomerTimelineEvent)
        .filter(CustomerTimelineEvent.customer_id == customer.id)
        .order_by(CustomerTimelineEvent.created_at.asc(), CustomerTimelineEvent.id.asc())
        .all()
    )

    return {
        "generated_at": _iso(datetime.now(UTC)),
        "schema_version": 1,
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
                "total_amount": _money_text(order.total_amount),
                "delivery_price": _money_text(order.delivery_price),
                "discount_amount": _money_text(order.discount_amount),
                "loyalty_points_redeemed": _money_text(order.loyalty_points_redeemed),
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
                        "price": _money_text(item.price),
                    }
                    for item in sorted(order.items, key=lambda item: item.id)
                ],
                "payments": [
                    {
                        "provider": payment.provider,
                        "provider_payment_id": payment.provider_payment_id,
                        "status": payment.status,
                        "amount": _money_text(payment.amount),
                        "created_at": _iso(payment.created_at),
                    }
                    for payment in sorted(order.payments, key=lambda payment: payment.id)
                ],
                "returns": [
                    {
                        "id": return_request.id,
                        "reason": return_request.reason,
                        "status": return_request.status,
                        "refund_amount": _money_text(return_request.refund_amount),
                        "created_at": _iso(return_request.created_at),
                    }
                    for return_request in sorted(order.returns, key=lambda request: request.id)
                ],
            }
            for order in orders
        ],
        "carts": [
            {
                "id": cart.id,
                "status": cart.status,
                "referral_code": cart.referral_code,
                "loyalty_points_to_redeem": _money_text(cart.loyalty_points_to_redeem),
                "created_at": _iso(cart.created_at),
                "items": [
                    {
                        "product_id": item.product_id,
                        "variant_id": item.variant_id,
                        "quantity": item.quantity,
                    }
                    for item in sorted(cart.items, key=lambda item: item.id)
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
                    "total_spent": _money_text(profile.total_spent),
                    "average_order_value": _money_text(profile.average_order_value),
                    "loyalty_points": _money_text(profile.loyalty_points),
                    "vip": profile.vip,
                }
                if profile
                else None
            ),
            "transactions": [
                {
                    "order_id": row.order_id,
                    "points_delta": _money_text(row.points_delta),
                    "reason": row.reason,
                    "created_at": _iso(row.created_at),
                }
                for row in loyalty
            ],
        },
        "referral_codes": [
            {
                "code": row.code,
                "reward_points": _money_text(row.reward_points),
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
                "payload": _json_safe(row.payload),
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
    if str(customer.telegram_id or "").startswith("deleted:"):
        return {
            "already_anonymized": True,
            "orders_anonymized": 0,
            "returns_redacted": 0,
            "tickets_redacted": 0,
            "carts_removed": 0,
            "consents_withdrawn": 0,
        }

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
        "already_anonymized": False,
        "orders_anonymized": len(orders),
        "returns_redacted": len(return_requests),
        "tickets_redacted": len(tickets),
        "carts_removed": removed_carts,
        "consents_withdrawn": withdrawn,
    }


def mark_privacy_processed(req: PrivacyRequest, result_url: str = "") -> None:
    if req.status != "processing":
        raise ValueError("Privacy request must be processing before completion")
    req.status = "processed"
    req.result_url = str(result_url or "").strip()[:2048]
    req.processed_at = datetime.now(UTC).replace(tzinfo=None)
