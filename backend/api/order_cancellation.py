from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload, selectinload

from ..database import get_db
from ..models import Customer, FulfillmentTask, Order
from ..schemas import OrderOut, OrderStatusUpdate
from ..security import get_current_admin, get_current_customer
from ..services.audit import log_admin_action
from ..services.fulfillment import ensure_fulfillment_task, update_fulfillment_status
from ..services.order_cancellation import cancel_order_before_settlement
from ..services.rbac import require_permission

router = APIRouter(tags=["order-cancellation"])

_GENERIC_FULFILLMENT_START_STATUS = "assembling"
_GENERIC_ORDER_WORKFLOW_MESSAGE = (
    "Generic order PATCH may only start fulfillment for a paid order; "
    "later fulfillment, shipment, tracking, payment, refund, and cancellation "
    "transitions are controlled by dedicated workflows"
)


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


def _load_locked_fulfillment_task(db: Session, order_id: int) -> FulfillmentTask | None:
    return (
        db.query(FulfillmentTask)
        .filter(FulfillmentTask.order_id == order_id)
        .with_for_update()
        .first()
    )


def _load_order_response(
    db: Session,
    order_id: int,
    customer_id: int | None = None,
) -> Order | None:
    query = db.query(Order).options(joinedload(Order.items)).filter(Order.id == order_id)
    if customer_id is not None:
        query = query.filter(Order.customer_id == customer_id)
    return query.first()


def _generic_order_workflow_error(order_id: int, managed_fields: list[str]) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "message": _GENERIC_ORDER_WORKFLOW_MESSAGE,
            "managed_fields": managed_fields,
            "safe_cancellation_endpoint": f"/api/admin/orders/{order_id}/cancel-safe",
            "fulfillment_endpoint": "/api/fulfillment/tasks/{task_id}",
        },
    )


@router.patch(
    "/admin/orders/{order_id}",
    response_model=OrderOut,
    name="start_admin_fulfillment_via_generic_patch",
)
def start_admin_fulfillment_via_generic_patch(
    order_id: int,
    payload: OrderStatusUpdate,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Compatibility gateway for the one safe generic transition: paid -> assembling.

    The Admin order row may start picking through this compatibility endpoint, but
    the mutation is delegated to the authoritative fulfillment state machine. All
    later fulfillment, shipment, tracking and financial transitions must use their
    dedicated workflows.
    """

    require_permission(db, admin, "orders.write")
    require_permission(db, admin, "fulfillment.write")

    changed = payload.model_dump(exclude_unset=True)
    forbidden_fields = sorted(
        field
        for field in ("delivery_status", "tracking_number")
        if field in changed
        and changed[field] is not None
        and str(changed[field]).strip()
    )
    if forbidden_fields:
        raise _generic_order_workflow_error(order_id, forbidden_fields)

    requested_status = str(changed.get("status") or "").strip().lower()
    if not requested_status:
        raise HTTPException(
            status_code=400,
            detail="Generic admin order PATCH requires status=assembling",
        )
    if requested_status != _GENERIC_FULFILLMENT_START_STATUS:
        raise _generic_order_workflow_error(order_id, ["status"])

    try:
        order = _load_locked_order(db, order_id)
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        task = _load_locked_fulfillment_task(db, order_id)
        if order.status == "assembling":
            if task is not None and task.status in {"picking", "packed", "blocked"}:
                db.rollback()
                return _load_order_response(db, order_id)
            raise HTTPException(
                status_code=409,
                detail="Order fulfillment state is inconsistent and requires review",
            )

        if order.status != "paid" or order.payment_status not in {"paid", "partially_refunded"}:
            raise HTTPException(
                status_code=409,
                detail="Only a settled paid order may enter fulfillment",
            )
        if not order.items:
            raise HTTPException(
                status_code=409,
                detail="Paid order has no items and requires review",
            )

        if task is None:
            task = ensure_fulfillment_task(db, order)
        if task.assigned_admin_id is None:
            task.assigned_admin_id = admin.id

        previous_task_status = task.status
        update_fulfillment_status(db, task, "picking")
        log_admin_action(
            db,
            admin,
            "fulfillment.task.update",
            "fulfillment_task",
            task.id,
            {
                "from_status": previous_task_status,
                "status": task.status,
                "assigned_admin_id": task.assigned_admin_id,
                "source": "admin_order_gateway",
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
