from sqlalchemy.orm import Session

from ..models import Notification, Order

_TELEGRAM_MESSAGE_LIMIT = 4096


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


def _notification_already_exists(
    db: Session,
    telegram_id: str,
    message: str,
) -> bool:
    # SQLAlchemy queries autoflush in production, but checking ``db.new`` first
    # also protects sessions configured with autoflush disabled and repeated
    # producer calls made before the first query or explicit flush.
    for candidate in getattr(db, "new", ()):
        if not isinstance(candidate, Notification):
            continue
        if candidate.telegram_id == telegram_id and candidate.message == message:
            return True

    return (
        db.query(Notification.id)
        .filter(
            Notification.telegram_id == telegram_id,
            Notification.message == message,
        )
        .first()
        is not None
    )


def queue_notification(
    db: Session,
    telegram_id: str,
    message: str,
    *,
    deduplicate: bool = False,
) -> bool:
    normalized_id = _normalize_telegram_id(telegram_id)
    normalized_message = _normalize_message(message)
    if not normalized_id or not normalized_message:
        return False
    if deduplicate and _notification_already_exists(db, normalized_id, normalized_message):
        return False

    db.add(
        Notification(
            telegram_id=normalized_id,
            message=normalized_message,
            status="pending",
        )
    )
    return True


def queue_order_paid(db: Session, order: Order) -> bool:
    return queue_notification(
        db,
        order.customer.telegram_id,
        f"✅ Заказ #{order.id} оплачен. Сумма: {order.total_amount:.2f} {order.currency}",
        deduplicate=True,
    )


def queue_order_status(db: Session, order: Order) -> bool:
    return queue_notification(
        db,
        order.customer.telegram_id,
        f"📦 Заказ #{order.id}: статус {order.status}, доставка {order.delivery_status}.",
        deduplicate=True,
    )
