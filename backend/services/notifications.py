from sqlalchemy.orm import Session

from ..models import Notification, Order
from ..notification_models import NotificationEventKey

_TELEGRAM_MESSAGE_LIMIT = 4096
_EVENT_KEY_LIMIT = 255


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
) -> bool:
    normalized_id = _normalize_telegram_id(telegram_id)
    normalized_message = _normalize_message(message)
    normalized_event_key = _normalize_event_key(event_key)
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
    )


def queue_order_status(db: Session, order: Order) -> bool:
    return queue_notification(
        db,
        order.customer.telegram_id,
        f"📦 Заказ #{order.id}: статус {order.status}, доставка {order.delivery_status}.",
        event_key=(
            f"order:{order.id}:status:{order.status}:delivery:{order.delivery_status}"
        ),
    )
