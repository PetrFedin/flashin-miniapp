from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Customer, Order, ReturnRequest
from ..security import get_current_admin
from ..services.rbac import require_permission
from ..services.refund_state import refund_money

router = APIRouter(prefix="/admin/returns", tags=["admin-returns"])


@router.get("")
def list_admin_returns(
    status: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=200, ge=1, le=500),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "orders.read")

    refunded_totals = (
        db.query(
            ReturnRequest.order_id.label("order_id"),
            func.coalesce(func.sum(ReturnRequest.refund_amount), 0).label("refunded_total"),
        )
        .filter(ReturnRequest.status.in_(["approved", "approved_partial"]))
        .group_by(ReturnRequest.order_id)
        .subquery()
    )
    query = (
        db.query(
            ReturnRequest,
            Order,
            Customer,
            func.coalesce(refunded_totals.c.refunded_total, 0),
        )
        .join(Order, Order.id == ReturnRequest.order_id)
        .join(Customer, Customer.id == ReturnRequest.customer_id)
        .outerjoin(refunded_totals, refunded_totals.c.order_id == Order.id)
    )
    normalized_status = (status or "").strip().lower()
    if normalized_status:
        query = query.filter(ReturnRequest.status == normalized_status)

    rows = (
        query.order_by(ReturnRequest.created_at.desc(), ReturnRequest.id.desc())
        .limit(limit)
        .all()
    )
    zero = refund_money(0, "zero")
    result = []
    for return_request, order, customer, raw_refunded_total in rows:
        refunded_total = refund_money(raw_refunded_total, "refunded total")
        refundable_balance = max(
            refund_money(order.total_amount, "order total") - refunded_total,
            zero,
        )
        result.append(
            {
                "id": return_request.id,
                "order_id": order.id,
                "customer_id": customer.id,
                "customer_name": " ".join(
                    value for value in [customer.first_name, customer.last_name] if value
                ).strip(),
                "customer_username": customer.username,
                "customer_phone": customer.phone,
                "reason": return_request.reason,
                "status": return_request.status,
                "refund_amount": return_request.refund_amount,
                "provider_refund_id": return_request.provider_refund_id,
                "order_total": order.total_amount,
                "refunded_total": float(refunded_total),
                "refundable_balance": float(refundable_balance),
                "currency": order.currency,
                "order_status": order.status,
                "payment_status": order.payment_status,
                "created_at": return_request.created_at,
            }
        )
    return result
