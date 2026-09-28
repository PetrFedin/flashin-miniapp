from sqlalchemy.orm import Session

from ..models import Notification, Order
from ..notification_models import NotificationEventKey, NotificationPolicyContext

_TELEGRAM_MESSAGE_LIMIT = 4096
_EVENT_KEY_LIMIT = 255

NOTIFICATION_PURPOSE_TRANSACTIONAL = "transactional"
NOTIFICATION_PURPOSE_MARKETING = "marketing"
_NOTIFICATION_PURPOSES = {
    NOTIFICATION_PURPOSE_TRANSACTIONAL,
    NOTIFICATION_PURPOSE_MARKETING,
}


def _normalize_telegram_id(value: str) -> str | None:
    normalized = str(value or "").strip()
    if not normalized or normalized.startswith("deleted:"):
        return None
    try:
        numeric_id = int(normalized)
    except (TypeError, ValueError):
        return None
    if numeric_id == 0:
        return None
    return str(numeric_id)


def _normalize_message(message: str) -> str | None:
    normalized = str(message or "").strip()
    if not normalized:
        return None
    if len(normalized) > _TELEGRAM_MESSAGE_LIMIT:
        normalized = normalized[: _TELEGRAM_MESSAGE_LIMIT - 1].rstrip() + "…"
    return normalized


def _normalize_event_key(event_key: str | None) -> str | None:
    if event_key is None:
        return None
    normalized = str(event_key).strip().lower()
    if not normalized:
        raise ValueError("Notification event key cannot be empty")
    if len(normalized) > _EVENT_KEY_LIMIT:
        raise ValueError("Notification event key is too long")
    return normalized


def _normalize_optional_id(value: int | None, *, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"Notification {field} must be a positive integer")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Notification {field} must be a positive integer") from exc
    if normalized <= 0:
        raise ValueError(f"Notification {field} must be a positive integer")
    return normalized


def _normalize_purpose(purpose: str) -> str:
    normalized = str(purpose or "").strip().lower()
    if normalized not in _NOTIFICATION_PURPOSES:
        raise ValueError("Unsupported notification purpose")
    return normalized


def _event_key_already_exists(db: Session, event_key: str) -> bool:
    # Protect repeated producer calls made before the first flush. The unique
    # database constraint handles persisted rows and is the final concurrency
    # boundary for deterministic events.
    for candidate in getattr(db, "new", ()):
        if isinstance(candidate, NotificationEventKey) and candidate.event_key == event_key:
            return True

    return (
        db.query(NotificationEventKey.id)
        .filter(NotificationEventKey.event_key == event_key)
        .first()
        is not None
    )


def queue_notification(
    db: Session,
    telegram_id: str,
    message: str,
    *,
    event_key: str | None = None,
    purpose: str = NOTIFICATION_PURPOSE_TRANSACTIONAL,
    customer_id: int | None = None,
    campaign_id: int | None = None,
) -> bool:
    normalized_id = _normalize_telegram_id(telegram_id)
    normalized_message = _normalize_message(message)
    normalized_event_key = _normalize_event_key(event_key)
    normalized_purpose = _normalize_purpose(purpose)
    normalized_customer_id = _normalize_optional_id(customer_id, field="customer_id")
    normalized_campaign_id = _normalize_optional_id(campaign_id, field="campaign_id")

    if normalized_purpose == NOTIFICATION_PURPOSE_MARKETING and normalized_customer_id is None:
        raise ValueError("Marketing notification requires customer_id")
    if normalized_campaign_id is not None and normalized_purpose != NOTIFICATION_PURPOSE_MARKETING:
        raise ValueError("campaign_id is only valid for marketing notifications")
    if not normalized_id or not normalized_message:
        return False
    if normalized_event_key and _event_key_already_exists(db, normalized_event_key):
        return False

    notification = Notification(
        telegram_id=normalized_id,
        message=normalized_message,
        status="pending",
    )
    db.add(notification)
    db.add(
        NotificationPolicyContext(
            notification=notification,
            purpose=normalized_purpose,
            customer_id=normalized_customer_id,
            campaign_id=normalized_campaign_id,
        )
    )
    if normalized_event_key:
        db.add(
            NotificationEventKey(
                event_key=normalized_event_key,
                notification=notification,
            )
        )
    return True


def queue_order_paid(db: Session, order: Order) -> bool:
    return queue_notification(
        db,
        order.customer.telegram_id,
        f"✅ Заказ #{order.id} оплачен. Сумма: {order.total_amount:.2f} {order.currency}",
        event_key=f"order:{order.id}:paid",
        purpose=NOTIFICATION_PURPOSE_TRANSACTIONAL,
        customer_id=order.customer_id,
    )


def queue_order_status(db: Session, order: Order) -> bool:
    return queue_notification(
        db,
        order.customer.telegram_id,
        f"📦 Заказ #{order.id}: статус {order.status}, доставка {order.delivery_status}.",
        event_key=(
            f"order:{order.id}:status:{order.status}:delivery:{order.delivery_status}"
        ),
        purpose=NOTIFICATION_PURPOSE_TRANSACTIONAL,
        customer_id=order.customer_id,
    )


def queue_order_refund(
    db: Session,
    order: Order,
    *,
    return_id: int,
    amount: float,
    full_refund: bool,
) -> bool:
    label = "полностью возвращена" if full_refund else "частично возвращена"
    return queue_notification(
        db,
        order.customer.telegram_id,
        (
            f"↩️ По заказу #{order.id} сумма {float(amount):.2f} {order.currency} "
            f"{label}."
        ),
        event_key=f"order:{order.id}:refund:{int(return_id)}:succeeded",
        purpose=NOTIFICATION_PURPOSE_TRANSACTIONAL,
        customer_id=order.customer_id,
    )
