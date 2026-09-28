from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO
from typing import BinaryIO, Callable, Iterator, TypeVar

from sqlalchemy.orm import Session

from ..auth_models import CustomerAuthEvent, CustomerSession
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
    SupportTicket,
    WishlistItem,
)

PRIVACY_EXPORT_SCHEMA_VERSION = "flashin.customer-data-export.v1"
PRIVACY_EXPORT_FILENAME = "flashin_customer_export_v1.json"
PRIVACY_EXPORT_BATCH_SIZE = 200
MONEY_SCALE = 2
POINTS_SCALE = 4


class PrivacyExportUnavailable(RuntimeError):
    """The authenticated subject no longer has an exportable active identity."""


T = TypeVar("T")


def _write(stream: BinaryIO, text: str) -> None:
    stream.write(text.encode("utf-8"))


def _json(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        aware = value.replace(tzinfo=UTC)
    else:
        aware = value.astimezone(UTC)
    return aware.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _decimal_string(value, *, scale: int) -> str | None:
    if value is None:
        return None
    try:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Privacy export numeric value is invalid") from exc
    if not decimal_value.is_finite():
        raise ValueError("Privacy export numeric value must be finite")
    quantum = Decimal("1").scaleb(-int(scale))
    normalized = decimal_value.quantize(quantum, rounding=ROUND_HALF_UP)
    return format(normalized, f".{int(scale)}f")


def _money(value) -> str | None:
    return _decimal_string(value, scale=MONEY_SCALE)


def _points(value) -> str | None:
    return _decimal_string(value, scale=POINTS_SCALE)


def _iter_customer_rows(
    db: Session,
    model,
    *,
    customer_id: int,
    batch_size: int = PRIVACY_EXPORT_BATCH_SIZE,
) -> Iterator:
    last_id = 0
    while True:
        rows = (
            db.query(model)
            .filter(model.customer_id == customer_id, model.id > last_id)
            .order_by(model.id.asc())
            .limit(batch_size)
            .all()
        )
        if not rows:
            return
        for row in rows:
            yield row
            last_id = int(row.id)


def _iter_rows_by_filter(
    db: Session,
    model,
    predicate,
    *,
    batch_size: int = PRIVACY_EXPORT_BATCH_SIZE,
) -> Iterator:
    last_id = 0
    while True:
        rows = (
            db.query(model)
            .filter(predicate, model.id > last_id)
            .order_by(model.id.asc())
            .limit(batch_size)
            .all()
        )
        if not rows:
            return
        for row in rows:
            yield row
            last_id = int(row.id)


def _write_array(
    stream: BinaryIO,
    rows: Iterator[T],
    render: Callable[[T], dict],
) -> None:
    _write(stream, "[")
    first = True
    for row in rows:
        if not first:
            _write(stream, ",")
        _write(stream, _json(render(row)))
        first = False
    _write(stream, "]")


def _order_payload(order: Order) -> dict:
    return {
        "id": order.id,
        "status": order.status,
        "payment_status": order.payment_status,
        "delivery_status": order.delivery_status,
        "total_amount": _money(order.total_amount),
        "delivery_price": _money(order.delivery_price),
        "discount_amount": _money(order.discount_amount),
        "loyalty_points_redeemed": _points(order.loyalty_points_redeemed),
        "loyalty_discount_amount": _money(order.loyalty_discount_amount),
        "currency": order.currency,
        "delivery_type": order.delivery_type,
        "address": order.address,
        "comment": order.comment,
        "tracking_number": order.tracking_number,
        "created_at": _timestamp(order.created_at),
        "items": [
            {
                "id": item.id,
                "product_id": item.product_id,
                "variant_id": item.variant_id,
                "title": item.title,
                "size": item.size,
                "quantity": item.quantity,
                "price": _money(item.price),
            }
            for item in sorted(order.items, key=lambda row: row.id)
        ],
        "payments": [
            {
                "id": payment.id,
                "provider": payment.provider,
                "provider_payment_id": payment.provider_payment_id,
                "status": payment.status,
                "amount": _money(payment.amount),
                "created_at": _timestamp(payment.created_at),
            }
            for payment in sorted(order.payments, key=lambda row: row.id)
        ],
        "returns": [
            {
                "id": request.id,
                "reason": request.reason,
                "status": request.status,
                "provider_refund_id": request.provider_refund_id,
                "refund_amount": _money(request.refund_amount),
                "created_at": _timestamp(request.created_at),
            }
            for request in sorted(order.returns, key=lambda row: row.id)
        ],
    }


def _cart_payload(cart: Cart) -> dict:
    return {
        "id": cart.id,
        "status": cart.status,
        "referral_code": cart.referral_code,
        "loyalty_points_to_redeem": _points(cart.loyalty_points_to_redeem),
        "created_at": _timestamp(cart.created_at),
        "updated_at": _timestamp(cart.updated_at),
        "items": [
            {
                "id": item.id,
                "product_id": item.product_id,
                "variant_id": item.variant_id,
                "quantity": item.quantity,
                "created_at": _timestamp(item.created_at),
            }
            for item in sorted(cart.items, key=lambda row: row.id)
        ],
    }


def _assert_exportable_customer(customer: Customer) -> None:
    telegram_id = str(customer.telegram_id or "")
    if telegram_id.startswith("deleted:"):
        raise PrivacyExportUnavailable(
            "Anonymized customer identities cannot be exported"
        )


def write_customer_export(
    db: Session,
    customer: Customer,
    stream: BinaryIO,
    *,
    generated_at: datetime | None = None,
) -> None:
    """Write the complete customer export without constructing the whole graph in RAM.

    History sections use deterministic primary-key keyset batches. Nested order/cart
    children are bounded by a single order/cart at a time. Decimal persistence values
    are rendered as exact strings rather than binary JSON floats.
    """

    _assert_exportable_customer(customer)
    customer_id = int(customer.id)
    generated = generated_at or utcnow_naive()

    _write(stream, "{")
    _write(stream, '"schema_version":' + _json(PRIVACY_EXPORT_SCHEMA_VERSION))
    _write(stream, ',"generated_at":' + _json(_timestamp(generated)))
    _write(
        stream,
        ',"representation":'
        + _json(
            {
                "money": "decimal-string-2dp",
                "loyalty_points": "decimal-string-4dp",
                "timestamps": "ISO-8601 UTC string",
                "nullable": "JSON null",
                "ordering": "ascending local record id",
            }
        ),
    )
    _write(
        stream,
        ',"customer":'
        + _json(
            {
                "id": customer.id,
                "telegram_id": customer.telegram_id,
                "username": customer.username,
                "first_name": customer.first_name,
                "last_name": customer.last_name,
                "phone": customer.phone,
                "email": customer.email,
                "created_at": _timestamp(customer.created_at),
                "status": "active",
            }
        ),
    )

    _write(stream, ',"authentication":{"sessions":')
    _write_array(
        stream,
        _iter_customer_rows(db, CustomerSession, customer_id=customer_id),
        lambda row: {
            "id": row.id,
            "created_at": _timestamp(row.created_at),
            "expires_at": _timestamp(row.expires_at),
            "revoked_at": _timestamp(row.revoked_at),
            "revoke_reason": row.revoke_reason,
        },
    )
    _write(stream, ',"events":')
    _write_array(
        stream,
        _iter_customer_rows(db, CustomerAuthEvent, customer_id=customer_id),
        lambda row: {
            "id": row.id,
            "session_id": row.session_id,
            "event_type": row.event_type,
            "created_at": _timestamp(row.created_at),
        },
    )
    _write(stream, "}")

    _write(stream, ',"orders":')
    _write_array(
        stream,
        _iter_customer_rows(db, Order, customer_id=customer_id),
        _order_payload,
    )

    _write(stream, ',"carts":')
    _write_array(
        stream,
        _iter_customer_rows(db, Cart, customer_id=customer_id),
        _cart_payload,
    )

    _write(stream, ',"wishlist":')
    _write_array(
        stream,
        _iter_customer_rows(db, WishlistItem, customer_id=customer_id),
        lambda row: {
            "id": row.id,
            "product_id": row.product_id,
            "created_at": _timestamp(row.created_at),
        },
    )

    _write(stream, ',"restock_subscriptions":')
    _write_array(
        stream,
        _iter_customer_rows(db, RestockSubscription, customer_id=customer_id),
        lambda row: {
            "id": row.id,
            "variant_id": row.variant_id,
            "active": row.active,
            "created_at": _timestamp(row.created_at),
        },
    )

    _write(stream, ',"consents":')
    _write_array(
        stream,
        _iter_customer_rows(db, ConsentRecord, customer_id=customer_id),
        lambda row: {
            "id": row.id,
            "type": row.consent_type,
            "granted": row.granted,
            "source": row.source,
            "created_at": _timestamp(row.created_at),
        },
    )

    _write(stream, ',"privacy_requests":')
    _write_array(
        stream,
        _iter_customer_rows(db, PrivacyRequest, customer_id=customer_id),
        lambda row: {
            "id": row.id,
            "type": row.request_type,
            "status": row.status,
            "result_url": row.result_url,
            "created_at": _timestamp(row.created_at),
            "processed_at": _timestamp(row.processed_at),
        },
    )

    profile = (
        db.query(CrmProfile)
        .filter(CrmProfile.customer_id == customer_id)
        .first()
    )
    _write(stream, ',"loyalty":{"profile":')
    _write(
        stream,
        _json(
            {
                "segment": profile.segment,
                "orders_count": profile.orders_count,
                "total_spent": _money(profile.total_spent),
                "average_order_value": _money(profile.average_order_value),
                "loyalty_points": _points(profile.loyalty_points),
                "vip": profile.vip,
                "last_order_at": _timestamp(profile.last_order_at),
                "updated_at": _timestamp(profile.updated_at),
            }
            if profile
            else None
        ),
    )
    _write(stream, ',"transactions":')
    _write_array(
        stream,
        _iter_customer_rows(db, LoyaltyTransaction, customer_id=customer_id),
        lambda row: {
            "id": row.id,
            "order_id": row.order_id,
            "points_delta": _points(row.points_delta),
            "reason": row.reason,
            "created_at": _timestamp(row.created_at),
        },
    )
    _write(stream, "}")

    _write(stream, ',"referral_codes":')
    _write_array(
        stream,
        _iter_customer_rows(db, ReferralCode, customer_id=customer_id),
        lambda row: {
            "id": row.id,
            "code": row.code,
            "reward_points": _points(row.reward_points),
            "used_count": row.used_count,
            "active": row.active,
            "created_at": _timestamp(row.created_at),
        },
    )

    _write(stream, ',"support_tickets":')
    _write_array(
        stream,
        _iter_customer_rows(db, SupportTicket, customer_id=customer_id),
        lambda row: {
            "id": row.id,
            "order_id": row.order_id,
            "subject": row.subject,
            "message": row.message,
            "status": row.status,
            "priority": row.priority,
            "created_at": _timestamp(row.created_at),
            "updated_at": _timestamp(row.updated_at),
        },
    )

    _write(stream, ',"timeline":')
    _write_array(
        stream,
        _iter_customer_rows(db, CustomerTimelineEvent, customer_id=customer_id),
        lambda row: {
            "id": row.id,
            "event_type": row.event_type,
            "title": row.title,
            "payload": row.payload,
            "created_at": _timestamp(row.created_at),
        },
    )

    _write(stream, ',"notifications":')
    _write_array(
        stream,
        _iter_rows_by_filter(
            db,
            Notification,
            Notification.telegram_id == customer.telegram_id,
        ),
        lambda row: {
            "id": row.id,
            "message": row.message,
            "status": row.status,
            "created_at": _timestamp(row.created_at),
            "sent_at": _timestamp(row.sent_at),
        },
    )
    _write(stream, "}")


def build_customer_export(
    db: Session,
    customer: Customer,
    *,
    generated_at: datetime | None = None,
) -> dict:
    """Compatibility helper for tests/small callers; the HTTP route uses streaming."""

    buffer = BytesIO()
    write_customer_export(db, customer, buffer, generated_at=generated_at)
    return json.loads(buffer.getvalue().decode("utf-8"))
