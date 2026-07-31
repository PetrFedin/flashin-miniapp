from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session, joinedload, selectinload

from .admin import router as admin_router
from ..database import get_db
from ..models import (
    Customer,
    LoyaltyRedemptionHold,
    Order,
    Payment,
    PromoCode,
)
from ..payment_attempt_models import PaymentCreationAttempt
from ..schemas import OrderOut, OrderStatusUpdate
from ..security import get_current_admin, get_current_customer
from ..services.audit import log_admin_action
from ..services.inventory import release_variant
from ..services.notifications import queue_order_status
from ..services.rbac import require_permission

router = APIRouter(tags=["order-cancellation"])
_MANAGED_ADMIN_ORDER_FIELDS = frozenset(
    {"status", "delivery_status", "tracking_number"}
)
_WORKFLOW_BOUNDARY_MESSAGE = (
    "Order status, delivery status, and tracking are controlled by dedicated "
    "payment, fulfillment, shipment, refund, or safe-cancellation workflows"
)
_ADMIN_ORDER_PATCH_PATH = "/admin/orders/{order_id}"


def _load_order(db: Session, order_id: int, customer_id: int | None = None) -> Order | None:
    query = (
        db.query(Order)
        .options(selectinload(Order.items), selectinload(Order.customer))
        .filter(Order.id == order_id)
    )
    if customer_id is not None:
        query = query.filter(Order.customer_id == customer_id)
    return query.with_for_update().first()


def _cancel_before_payment(db: Session, order: Order) -> None:
    if order.status == "cancelled":
        return
    if order.status != "created" or order.payment_status != "pending":
        raise HTTPException(
            status_code=409,
            detail="Order can be cancelled directly only before payment starts",
        )

    payment_exists = (
        db.query(Payment.id)
        .filter(Payment.order_id == order.id)
        .with_for_update()
        .first()
        is not None
    )
    payment_attempt_exists = (
        db.query(PaymentCreationAttempt.id)
        .filter(PaymentCreationAttempt.order_id == order.id)
        .with_for_update()
        .first()
        is not None
    )
    if payment_exists or payment_attempt_exists:
        raise HTTPException(
            status_code=409,
            detail="Payment flow already exists; reconcile payment or use refund flow",
        )

    for item in sorted(order.items, key=lambda row: row.variant_id):
        release_variant(db, item.variant_id, item.quantity)

    if order.promo_code_id:
        promo = (
            db.query(PromoCode)
            .filter(PromoCode.id == order.promo_code_id)
            .with_for_update()
            .first()
        )
        if promo:
            promo.used_count = max(int(promo.used_count or 0) - 1, 0)

    holds = (
        db.query(LoyaltyRedemptionHold)
        .filter(
            LoyaltyRedemptionHold.customer_id == order.customer_id,
            LoyaltyRedemptionHold.order_id == order.id,
            LoyaltyRedemptionHold.status == "reserved",
        )
        .with_for_update()
        .all()
    )
    for hold in holds:
        hold.status = "released"
        hold.released_at = datetime.utcnow()

    order.status = "cancelled"
    order.payment_status = "cancelled"
    order.delivery_status = "cancelled"
    queue_order_status(db, order)


def reject_generic_admin_order_update(
    order_id: int,
    payload: OrderStatusUpdate,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Reject generic mutations owned by dedicated order workflows."""
    require_permission(db, admin, "orders.write")
    order_exists = db.query(Order.id).filter(Order.id == order_id).first() is not None
    db.rollback()
    if not order_exists:
        raise HTTPException(status_code=404, detail="Order not found")

    changed = payload.model_dump(exclude_unset=True)
    managed_fields = sorted(
        field
        for field in _MANAGED_ADMIN_ORDER_FIELDS
        if field in changed
        and changed[field] is not None
        and str(changed[field]).strip()
    )
    if managed_fields:
        raise HTTPException(
            status_code=409,
            detail={
                "message": _WORKFLOW_BOUNDARY_MESSAGE,
                "managed_fields": managed_fields,
                "safe_cancellation_endpoint": (
                    f"/api/admin/orders/{order_id}/cancel-safe"
                ),
            },
        )
    raise HTTPException(
        status_code=400,
        detail={
            "message": "Generic admin order PATCH has no editable fields",
            "managed_fields": [],
        },
    )


def _replace_legacy_admin_order_patch() -> None:
    matching = [
        route
        for route in admin_router.routes
        if isinstance(route, APIRoute)
        and route.path == _ADMIN_ORDER_PATCH_PATH
        and "PATCH" in route.methods
    ]
    guarded = [
        route
        for route in matching
        if route.endpoint is reject_generic_admin_order_update
    ]
    legacy = [
        route
        for route in matching
        if route.endpoint is not reject_generic_admin_order_update
    ]

    if len(guarded) == 1 and not legacy:
        return
    if guarded or len(legacy) != 1:
        raise RuntimeError(
            "Expected exactly one legacy generic admin order PATCH route"
        )

    admin_router.routes.remove(legacy[0])
    admin_router.add_api_route(
        "/orders/{order_id}",
        reject_generic_admin_order_update,
        methods=["PATCH"],
        response_model=OrderOut,
        name="reject_generic_admin_order_update",
    )


_replace_legacy_admin_order_patch()


@router.post("/orders/{order_id}/cancel-safe", response_model=OrderOut)
def cancel_customer_order_safely(
    order_id: int,
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    try:
        order = _load_order(db, order_id, customer.id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        _cancel_before_payment(db, order)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise

    return (
        db.query(Order)
        .options(joinedload(Order.items))
        .filter(Order.id == order_id, Order.customer_id == customer.id)
        .first()
    )


@router.post("/admin/orders/{order_id}/cancel-safe", response_model=OrderOut)
def cancel_admin_order_safely(
    order_id: int,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "orders.write")
    try:
        order = _load_order(db, order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        previous_status = order.status
        _cancel_before_payment(db, order)
        log_admin_action(
            db,
            admin,
            "order.cancel_before_payment",
            "order",
            order.id,
            {"from_status": previous_status, "payment_status": order.payment_status},
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise

    return (
        db.query(Order)
        .options(joinedload(Order.items))
        .filter(Order.id == order_id)
        .first()
    )
