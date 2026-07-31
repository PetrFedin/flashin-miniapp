from datetime import timedelta

from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import utcnow_naive
from ..models import Cart, Notification
from ..services.inventory import snapshot_inventory


def queue_abandoned_cart_notifications(db: Session) -> int:
    settings = get_settings()
    now = utcnow_naive()
    cutoff = now - timedelta(minutes=settings.abandoned_cart_minutes)
    carts = (
        db.query(Cart)
        .filter(Cart.status == "active", Cart.updated_at <= cutoff, Cart.abandoned_notified_at == None)
        .all()
    )
    count = 0
    for cart in carts:
        if not cart.items or not cart.customer.telegram_id:
            continue
        db.add(Notification(
            telegram_id=cart.customer.telegram_id,
            message="🛒 Вы оставили вещи в корзине FLASHIN. Вернитесь, пока размер ещё в наличии.",
            status="pending",
        ))
        cart.abandoned_notified_at = now
        count += 1
    db.commit()
    return count


def create_inventory_snapshot(db: Session) -> int:
    count = snapshot_inventory(db, source="scheduled")
    db.commit()
    return count
