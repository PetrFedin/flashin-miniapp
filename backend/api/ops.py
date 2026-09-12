from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db, utcnow_naive
from ..models import Cart, Notification, ProductVariant
from ..schemas import AbandonedCartOut, InventorySnapshotOut
from ..security import get_current_admin
from ..services.inventory import snapshot_inventory
from ..services.order_lifecycle_moysklad_contract import enforce_moysklad_lifecycle_contract
from ..services.order_lifecycle_payment_state_contract import enforce_settled_order_payment_state_contract
from ..services.order_lifecycle_reconciliation import evaluate_order_lifecycle
from ..services.order_lifecycle_signals import apply_operational_signals
from ..services.order_operations_trace import build_order_operations_trace
from ..services.pilot_observability import build_pilot_operations_status
from ..services.pilot_readiness import build_pilot_readiness
from ..services.rbac import require_permission

router = APIRouter(prefix="/ops", tags=["ops"])


@router.get("/pilot-runtime")
def pilot_runtime_status(
    response: Response,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "security.read")
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return build_pilot_operations_status(db, get_settings())


@router.get("/pilot-readiness")
def pilot_readiness_status(
    request: Request,
    response: Response,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Sanitized, read-only verdict for accepting the next controlled pilot order."""

    require_permission(db, admin, "security.read")
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    readiness = build_pilot_readiness(db, get_settings())
    readiness["request_id"] = getattr(request.state, "request_id", "")
    return readiness


@router.get("/orders/{order_id}/trace")
def order_operations_trace(
    order_id: int,
    request: Request,
    response: Response,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Sanitized, read-only incident trace plus deterministic lifecycle verdicts."""

    require_permission(db, admin, "orders.read")
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    trace = build_order_operations_trace(db, order_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Order not found")
    trace["schema_version"] = 3
    reconciliation = enforce_settled_order_payment_state_contract(
        evaluate_order_lifecycle(trace),
        trace,
    )
    reconciliation = enforce_moysklad_lifecycle_contract(reconciliation, trace)
    trace["reconciliation"] = apply_operational_signals(reconciliation, trace)
    trace["request_id"] = getattr(request.state, "request_id", "")
    return trace


@router.get("/abandoned-carts", response_model=list[AbandonedCartOut])
def abandoned_carts(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "customers.read")
    settings = get_settings()
    cutoff = utcnow_naive() - timedelta(minutes=settings.abandoned_cart_minutes)
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
    require_permission(db, admin, "customers.read")
    require_permission(db, admin, "notifications.retry")
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
        if not cart.items:
            continue
        db.add(Notification(
            telegram_id=cart.customer.telegram_id,
            message="🛒 Вы оставили вещи в корзине FLASHIN. Вернитесь, пока размер ещё в наличии.",
            status="pending",
        ))
        cart.abandoned_notified_at = now
        count += 1
    db.commit()
    return {"ok": True, "queued": count}


@router.get("/inventory/low-stock", response_model=list[InventorySnapshotOut])
def low_stock(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.read")
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