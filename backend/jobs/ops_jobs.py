from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Cart
from ..services.inventory import snapshot_inventory
from ..services.notifications import queue_notification

_ABANDONED_CART_BATCH_SIZE = 500


def _abandoned_cart_deduplication_key(cart: Cart) -> str:
    changed_at = cart.updated_at or cart.created_at
    version = changed_at.isoformat(timespec="microseconds") if changed_at else "unknown"
    return f"cart:{cart.id}:abandoned:{version}"


def queue_abandoned_cart_notifications(db: Session) -> int:
    settings = get_settings()
    now = datetime.utcnow()
    cutoff = now - timedelta(minutes=settings.abandoned_cart_minutes)
    carts = (
        db.query(Cart)
        .filter(
            Cart.status == "active",
            Cart.updated_at <= cutoff,
            Cart.abandoned_notified_at.is_(None),
        )
        .order_by(Cart.updated_at.asc(), Cart.id.asc())
        .with_for_update(skip_locked=True)
        .limit(_ABANDONED_CART_BATCH_SIZE)
        .all()
    )
    count = 0
    for cart in carts:
        if not cart.items or not cart.customer or not cart.customer.telegram_id:
            continue
        queued = queue_notification(
            db,
            cart.customer.telegram_id,
            "🛒 Вы оставили вещи в корзине FLASHIN. Вернитесь, пока размер ещё в наличии.",
            deduplication_key=_abandoned_cart_deduplication_key(cart),
        )
        if queued:
            cart.abandoned_notified_at = now
            count += 1
    db.commit()
    return count


def create_inventory_snapshot(db: Session) -> int:
    count = snapshot_inventory(db, source="scheduled")
    db.commit()
    return count
