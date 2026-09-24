from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Customer, Order, ReturnRequest
from ..reverse_logistics_models import ReturnLogisticsCase
from ..security import get_current_admin
from ..services.rbac import has_permission, require_permission
from ..services.refund_allocation import (
    reconcile_refund_allocations,
    refund_allocation_options,
    return_request_allocation_summary,
)
from ..services.refund_state import refund_money

router = APIRouter(prefix="/admin/returns", tags=["admin-returns"])


def _customer_fields(customer: Customer | None, visible: bool) -> dict:
    if not visible or customer is None:
        return {
            "customer_pii_visible": False,
            "customer_id": None,
            "customer_name": "",
            "customer_username": "",
            "customer_phone": "",
        }
    return {
        "customer_pii_visible": True,
        "customer_id": customer.id,
        "customer_name": " ".join(
            value for value in [customer.first_name, customer.last_name] if value
        ).strip(),
        "customer_username": customer.username,
        "customer_phone": customer.phone,
    }


@router.get("")
def list_admin_returns(
    status: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=200, ge=1, le=500),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "orders.read")
    can_read_customer = has_permission(db, admin, "customers.read")

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
            func.coalesce(refunded_totals.c.refunded_total, 0),
            ReturnLogisticsCase.status.label("physical_status"),
        )
        .join(Order, Order.id == ReturnRequest.order_id)
        .outerjoin(refunded_totals, refunded_totals.c.order_id == Order.id)
        .outerjoin(ReturnLogisticsCase, ReturnLogisticsCase.return_request_id == ReturnRequest.id)
    )
    normalized_status = (status or "").strip().lower()
    if normalized_status:
        query = query.filter(ReturnRequest.status == normalized_status)

    rows = (
        query.order_by(ReturnRequest.created_at.desc(), ReturnRequest.id.desc())
        .limit(limit)
        .all()
    )

    customers_by_id: dict[int, Customer] = {}
    if can_read_customer and rows:
        customer_ids = {return_request.customer_id for return_request, _, _, _ in rows}
        customers_by_id = {
            customer.id: customer
            for customer in db.query(Customer).filter(Customer.id.in_(customer_ids)).all()
        }

    zero = refund_money(0, "zero")
    result = []
    reconciliation_by_order: dict[int, dict] = {}
    for return_request, order, raw_refunded_total, physical_status in rows:
        refunded_total = refund_money(raw_refunded_total, "refunded total")
        refundable_balance = max(
            refund_money(order.total_amount, "order total") - refunded_total,
            zero,
        )
        customer = customers_by_id.get(return_request.customer_id) if can_read_customer else None
        reconciliation = reconciliation_by_order.get(int(order.id))
        if reconciliation is None:
            reconciliation = reconcile_refund_allocations(db, int(order.id))
            reconciliation_by_order[int(order.id)] = reconciliation
        allocation = return_request_allocation_summary(db, int(return_request.id))
        allocation_options = refund_allocation_options(
            db,
            order=order,
            exclude_return_id=int(return_request.id),
        )
        result.append(
            {
                "id": return_request.id,
                "order_id": order.id,
                **_customer_fields(customer, can_read_customer),
                "reason": return_request.reason,
                "status": return_request.status,
                "physical_status": str(physical_status or "not_started"),
                "refund_amount": return_request.refund_amount,
                "provider_refund_id": return_request.provider_refund_id,
                "order_total": order.total_amount,
                "refunded_total": float(refunded_total),
                "refundable_balance": float(refundable_balance),
                "currency": order.currency,
                "order_status": order.status,
                "payment_status": order.payment_status,
                "financial_allocation": allocation,
                "financial_allocation_options": allocation_options,
                "financial_physical_reconciliation": reconciliation,
                "created_at": return_request.created_at,
            }
        )
    return result
