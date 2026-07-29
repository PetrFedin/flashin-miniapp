from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload, selectinload

from ..database import get_db
from ..models import (
    Customer,
    LoyaltyRedemptionHold,
    Order,
    Payment,
    PromoCode,
)
from ..payment_attempt_models import PaymentCreationAttempt
from ..schemas import OrderOut
from ..security import get_current_admin, get_current_customer
from ..services.audit import log_admin_action
from ..services.inventory import release_variant
from ..services.notifications import queue_order_status
from ..services.rbac import require_permission

router = APIRouter(tags=["order-cancellation"])


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
