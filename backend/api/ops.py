from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..jobs.execution import run_sync_job
from ..jobs.ops_jobs import create_inventory_snapshot
from ..jobs.ops_jobs import queue_abandoned_cart_notifications as queue_abandoned_cart_job
from ..models import Cart, ProductVariant
from ..schemas import AbandonedCartOut, InventorySnapshotOut
from ..security import get_current_admin
from ..services.rbac import require_permission

router = APIRouter(prefix="/ops", tags=["ops"])


def _require_executed(outcome, operation: str) -> None:
    if outcome.status == "skipped":
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"{operation} is already running",
                "run_id": outcome.run_id,
            },
        )


@router.get("/abandoned-carts", response_model=list[AbandonedCartOut])
def abandoned_carts(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "notifications.read")
    settings = get_settings()
    cutoff = datetime.utcnow() - timedelta(minutes=settings.abandoned_cart_minutes)
    carts = (
        db.query(Cart)
        .filter(
            Cart.status == "active",
            Cart.updated_at <= cutoff,
            Cart.abandoned_notified_at.is_(None),
        )
        .order_by(Cart.updated_at.asc(), Cart.id.asc())
        .limit(500)
        .all()
    )
    result = []
    for cart in carts:
        total = sum(item.product.price * item.quantity for item in cart.items)
        result.append(
            AbandonedCartOut(
                cart_id=cart.id,
                customer_id=cart.customer_id,
                telegram_id=cart.customer.telegram_id,
                items_count=len(cart.items),
                total_amount=total,
            )
        )
    return result


@router.post("/abandoned-carts/queue-notifications")
def queue_abandoned_cart_notifications(
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "notifications.retry")
    outcome = run_sync_job(
        "abandoned-carts",
        queue_abandoned_cart_job,
        trigger="api",
    )
    _require_executed(outcome, "Abandoned-cart notification job")
    return {
        "ok": True,
        "status": outcome.status,
        "run_id": outcome.run_id,
        "queued": outcome.result or 0,
    }


@router.get("/inventory/low-stock", response_model=list[InventorySnapshotOut])
def low_stock(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.read")
    settings = get_settings()
    variants = (
        db.query(ProductVariant)
        .order_by(ProductVariant.id.asc())
        .limit(1000)
        .all()
    )
    result = []
    for variant in variants:
        if variant.available_qty <= settings.inventory_low_stock_threshold:
            result.append(
                InventorySnapshotOut(
                    variant_id=variant.id,
                    stock_qty=variant.stock_qty,
                    reserved_qty=variant.reserved_qty,
                    available_qty=variant.available_qty,
                    sku=variant.sku,
                    product_title=variant.product.title,
                )
            )
    return result


@router.post("/inventory/snapshot")
def inventory_snapshot(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "inventory.write")
    outcome = run_sync_job(
        "inventory-snapshot",
        create_inventory_snapshot,
        trigger="api",
    )
    _require_executed(outcome, "Inventory snapshot job")
    return {
        "ok": True,
        "status": outcome.status,
        "run_id": outcome.run_id,
        "snapshotted": outcome.result or 0,
    }
