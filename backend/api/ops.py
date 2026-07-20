from datetime import datetime, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..config import get_settings
from ..database import get_db
from ..models import Cart, Notification, ProductVariant
from ..schemas import AbandonedCartOut, InventorySnapshotOut
from ..security import get_current_admin
from ..services.inventory import snapshot_inventory
from ..services.rbac import require_permission

router = APIRouter(prefix="/ops", tags=["ops"])


@router.get("/abandoned-carts", response_model=list[AbandonedCartOut])
def abandoned_carts(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "operations.read")
    settings = get_settings()
    cutoff = datetime.utcnow() - timedelta(minutes=settings.abandoned_cart_minutes)
    carts = (
        db.query(Cart)
        .filter(Cart.status == "active", Cart.updated_at <= cutoff, Cart.abandoned_notified_at == None)
        .all()
    )
    result = []
    for cart in carts:
        total = sum(item.product.price * item.quantity for item in cart.items)
        result.append(AbandonedCartOut(
            cart_id=cart.id,
            customer_id=cart.customer_id,
            telegram_id=cart.customer.telegram_id,
            items_count=len(cart.items),
            total_amount=total,
        ))
    return result


@router.post("/abandoned-carts/queue-notifications")
def queue_abandoned_cart_notifications(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "operations.write")
    settings = get_settings()
    cutoff = datetime.utcnow() - timedelta(minutes=settings.abandoned_cart_minutes)
    carts = (
        db.query(Cart)
        .filter(Cart.status == "active", Cart.updated_at <= cutoff, Cart.abandoned_notified_at == None)
        .all()
    )
    count = 0
    for cart in carts:
        if not cart.items:
            continue
        db.add(Notification(
            telegram_id=cart.customer.telegram_id,
            message="🛒 Вы оставили вещи в корзине FLASHIN. Вернитесь, пока размер ещё в наличии.",
            status="pending",
        ))
        cart.abandoned_notified_at = datetime.utcnow()
        count += 1
    db.commit()
    return {"ok": True, "queued": count}


@router.get("/inventory/low-stock", response_model=list[InventorySnapshotOut])
def low_stock(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "inventory.read")
    settings = get_settings()
    variants = db.query(ProductVariant).all()
    result = []
    for v in variants:
        if v.available_qty <= settings.inventory_low_stock_threshold:
            result.append(InventorySnapshotOut(
                variant_id=v.id,
                stock_qty=v.stock_qty,
                reserved_qty=v.reserved_qty,
                available_qty=v.available_qty,
                sku=v.sku,
                product_title=v.product.title,
            ))
    return result


@router.post("/inventory/snapshot")
def inventory_snapshot(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "inventory.write")
    count = snapshot_inventory(db, source="admin")
    db.commit()
    return {"ok": True, "snapshotted": count}
