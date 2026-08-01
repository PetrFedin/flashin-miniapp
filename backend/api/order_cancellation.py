from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload, selectinload

from ..database import get_db
from ..models import Customer, Order
from ..schemas import OrderOut
from ..security import get_current_admin, get_current_customer
from ..services.audit import log_admin_action
from ..services.order_cancellation import cancel_order_before_settlement
from ..services.rbac import require_permission

router = APIRouter(tags=["order-cancellation"])


def _load_locked_order(
    db: Session,
    order_id: int,
    customer_id: int | None = None,
) -> Order | None:
    query = (
        db.query(Order)
        .options(selectinload(Order.items), selectinload(Order.customer))
        .filter(Order.id == order_id)
    )
    if customer_id is not None:
        query = query.filter(Order.customer_id == customer_id)
    return query.with_for_update().first()


def _load_order_response(
    db: Session,
    order_id: int,
    customer_id: int | None = None,
) -> Order | None:
    query = db.query(Order).options(joinedload(Order.items)).filter(Order.id == order_id)
    if customer_id is not None:
        query = query.filter(Order.customer_id == customer_id)
    return query.first()


@router.post(
    "/orders/{order_id}/cancel-safe",
    response_model=OrderOut,
    include_in_schema=False,
)
@router.post("/orders/{order_id}/cancel", response_model=OrderOut)
def cancel_customer_order(
    order_id: int,
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    try:
        order = _load_locked_order(db, order_id, customer.id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        cancel_order_before_settlement(db, order, source="manual")
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise

    return _load_order_response(db, order_id, customer.id)


@router.post(
    "/admin/orders/{order_id}/cancel-safe",
    response_model=OrderOut,
    include_in_schema=False,
)
@router.post("/admin/orders/{order_id}/cancel", response_model=OrderOut)
def cancel_admin_order(
    order_id: int,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "orders.write")
    try:
        order = _load_locked_order(db, order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        previous_status = order.status
        changed = cancel_order_before_settlement(db, order, source="manual")
        if changed:
            log_admin_action(
                db,
                admin,
                "order.cancel_before_payment",
                "order",
                order.id,
                {
                    "from_status": previous_status,
                    "payment_status": order.payment_status,
                },
            )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise

    return _load_order_response(db, order_id)
