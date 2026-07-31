from datetime import datetime
from hashlib import sha256

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Notification, Order
from ..notification_models import NotificationDeliveryState
from ..notification_statuses import (
    NOTIFICATION_PENDING,
    normalize_notification_deduplication_key,
    normalize_notification_message,
    normalize_telegram_id,
)


def _normalize_telegram_id(value: object) -> str | None:
    """Compatibility wrapper for existing callers and focused safety tests."""

    try:
        return normalize_telegram_id(value)
    except HTTPException:
        return None


def _normalize_message(value: object) -> str | None:
    """Compatibility wrapper that safely truncates to Telegram's text limit."""

    try:
        return normalize_notification_message(value, truncate=True)
    except HTTPException:
        return None


def _order_status_deduplication_key(order: Order) -> str:
    facts = "|".join(
        [
            str(order.id),
            str(order.status or ""),
            str(order.delivery_status or ""),
            str(order.tracking_number or ""),
        ]
    )
    return f"order:{order.id}:status:{sha256(facts.encode('utf-8')).hexdigest()}"


def queue_notification(
    db: Session,
    telegram_id: str,
    message: str,
    *,
    deduplication_key: str = "",
) -> bool:
    """Persist one notification and its delivery state atomically.

    A non-empty deduplication key is protected by a database unique index. The
    nested transaction means a duplicate business event does not poison the
    caller's wider order/payment transaction.
    """

    normalized_id = _normalize_telegram_id(telegram_id)
    normalized_message = _normalize_message(message)
    if not normalized_id or not normalized_message:
        return False
    try:
        normalized_key = normalize_notification_deduplication_key(deduplication_key)
    except HTTPException:
        return False

    if normalized_key:
        existing = (
            db.query(NotificationDeliveryState.id)
            .filter(NotificationDeliveryState.deduplication_key == normalized_key)
            .first()
        )
        if existing:
            return False

    notification = Notification(
        telegram_id=normalized_id,
        message=normalized_message,
        status=NOTIFICATION_PENDING,
        error="",
        sent_at=None,
    )
    state = NotificationDeliveryState(
        attempts=0,
        next_attempt_at=datetime.utcnow(),
        last_error="",
        deduplication_key=normalized_key,
        lease_token="",
    )

    if not normalized_key:
        db.add(notification)
        db.flush()
        state.notification_id = notification.id
        db.add(state)
        return True

    try:
        with db.begin_nested():
            db.add(notification)
            db.flush()
            state.notification_id = notification.id
            db.add(state)
            db.flush()
    except IntegrityError:
        duplicate = (
            db.query(NotificationDeliveryState.id)
            .filter(NotificationDeliveryState.deduplication_key == normalized_key)
            .first()
        )
        if duplicate:
            return False
        raise
    return True


def queue_order_paid(db: Session, order: Order) -> bool:
    return queue_notification(
        db,
        order.customer.telegram_id,
        f"✅ Заказ #{order.id} оплачен. Сумма: {order.total_amount:.2f} {order.currency}",
        deduplication_key=f"order:{order.id}:paid",
    )


def queue_order_status(db: Session, order: Order) -> bool:
    tracking = f", трек-номер {order.tracking_number}" if order.tracking_number else ""
    return queue_notification(
        db,
        order.customer.telegram_id,
        (
            f"📦 Заказ #{order.id}: статус {order.status}, "
            f"доставка {order.delivery_status}{tracking}."
        ),
        deduplication_key=_order_status_deduplication_key(order),
    )
