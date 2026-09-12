from datetime import timedelta

from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import utcnow_naive
from ..models import Cart
from ..services.inventory import snapshot_inventory
from ..services.notifications import NOTIFICATION_PURPOSE_MARKETING, queue_notification


def queue_abandoned_cart_notifications(db: Session) -> int:
    settings = get_settings()
    now = utcnow_naive()
    cutoff = now - timedelta(minutes=settings.abandoned_cart_minutes)
    carts = (
        db.query(Cart)
        .filter(Cart.status == "active", Cart.updated_at <= cutoff, Cart.abandoned_notified_at == None)
        .with_for_update(skip_locked=True)
        .all()
    )
    count = 0
    for cart in carts:
        if not cart.items or not cart.customer.telegram_id:
            continue
        queued = queue_notification(
            db,
            cart.customer.telegram_id,
            "🛒 Вы оставили вещи в корзине FLASHIN. Вернитесь, пока размер ещё в наличии.",
            purpose=NOTIFICATION_PURPOSE_MARKETING,
            customer_id=cart.customer_id,
        )
        if not queued:
            continue
        cart.abandoned_notified_at = now
        count += 1
    db.commit()
    return count


def create_inventory_snapshot(db: Session) -> int:
    count = snapshot_inventory(db, source="scheduled")
    db.commit()
    return count
