from sqlalchemy.orm import Session
from ..models import Notification, Order


def queue_notification(db: Session, telegram_id: str, message: str) -> None:
    if telegram_id:
        db.add(Notification(telegram_id=telegram_id, message=message, status="pending"))


def queue_order_paid(db: Session, order: Order) -> None:
    queue_notification(
        db,
        order.customer.telegram_id,
        f"✅ Заказ #{order.id} оплачен. Сумма: {order.total_amount:.2f} {order.currency}",
    )


def queue_order_status(db: Session, order: Order) -> None:
    queue_notification(
        db,
        order.customer.telegram_id,
        f"📦 Заказ #{order.id}: статус {order.status}, доставка {order.delivery_status}.",
    )
