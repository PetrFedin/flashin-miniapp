from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Customer, Order, Payment, ReturnRequest
from ..schemas import RefundApproveIn, ReturnCreate, ReturnOut
from ..security import get_current_admin, get_current_customer
from ..services.audit import log_admin_action
from ..services.loyalty import refund_redeemed_points
from ..services.payments import create_yookassa_refund, fetch_yookassa_refund
from ..services.rbac import require_permission

router = APIRouter(prefix="/returns", tags=["returns"])

_MONEY_STEP = Decimal("0.01")
_FINAL_RETURN_STATUSES = {"approved", "approved_partial"}
_REFUND_PENDING_STATUSES = {"processing", "refund_pending", "refund_retry_required"}


def _money(value: object, field: str) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(_MONEY_STEP, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"Invalid {field}")
    if not amount.is_finite():
        raise HTTPException(status_code=400, detail=f"Invalid {field}")
    return amount


def _clean_reason(value: str) -> str:
    reason = (value or "").strip()
    if len(reason) < 5:
        raise HTTPException(status_code=400, detail="Return reason is too short")
    if len(reason) > 2000:
        raise HTTPException(status_code=400, detail="Return reason is too long")
    return reason


def _provider_refund_amount(provider_refund: dict, currency: str) -> Decimal:
    amount = provider_refund.get("amount") or {}
    provider_currency = str(amount.get("currency") or "").upper()
    provider_amount = _money(amount.get("value"), "provider refund amount")
    if provider_currency != str(currency).upper():
        raise HTTPException(status_code=409, detail="Provider refund currency does not match order")
    return provider_amount


def _refund_response(ret: ReturnRequest, provider_status: str, *, idempotent: bool = False) -> dict:
    return {
        "ok": True,
        "refund_id": ret.provider_refund_id,
        "status": provider_status,
        "return_status": ret.status,
        "refund_amount": ret.refund_amount,
        "idempotent": idempotent,
    }


def _apply_provider_refund_status(
    db: Session,
    ret: ReturnRequest,
    order: Order,
    provider_status: str,
) -> None:
    normalized_status = provider_status.strip().lower()
    if normalized_status == "succeeded":
        full_refund = _money(ret.refund_amount, "refund amount") == _money(order.total_amount, "order total")
        ret.status = "approved" if full_refund else "approved_partial"
        order.status = "refunded" if full_refund else "partially_refunded"
        order.payment_status = "refunded" if full_refund else "partially_refunded"
        refund_redeemed_points(
            db,
            order.customer_id,
            order.id,
            order.loyalty_points_redeemed,
        )
    elif normalized_status == "canceled":
        ret.status = "failed"
        order.status = "refund_requested"
        order.payment_status = "paid"
    else:
        ret.status = "refund_pending"
        order.status = "refund_requested"
        order.payment_status = "refund_pending"


@router.post("", response_model=ReturnOut)
def create_return(
    payload: ReturnCreate,
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    try:
        order = (
            db.query(Order)
            .filter(Order.id == payload.order_id, Order.customer_id == customer.id)
            .with_for_update()
            .first()
        )
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        existing = (
            db.query(ReturnRequest)
            .filter(ReturnRequest.order_id == order.id)
            .with_for_update()
            .first()
        )
        if existing:
            return existing

        if order.payment_status not in {"paid", "partially_refunded"}:
            raise HTTPException(status_code=409, detail="Only paid orders can be returned")
        if order.status in {"cancelled", "refunded"}:
            raise HTTPException(status_code=409, detail="Order cannot be returned in its current status")

        ret = ReturnRequest(
            order_id=order.id,
            customer_id=customer.id,
            reason=_clean_reason(payload.reason),
            status="requested",
        )
        order.status = "refund_requested"
        db.add(ret)
        db.commit()
        db.refresh(ret)
        return ret
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        existing = db.query(ReturnRequest).filter(ReturnRequest.order_id == payload.order_id).first()
        if existing and existing.customer_id == customer.id:
            return existing
        raise HTTPException(status_code=409, detail="Return request already exists")
    except Exception:
        db.rollback()
        raise


@router.get("", response_model=list[ReturnOut])
def my_returns(customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    return (
        db.query(ReturnRequest)
        .filter(ReturnRequest.customer_id == customer.id)
        .order_by(ReturnRequest.created_at.desc())
        .all()
    )


@router.post("/admin/approve")
async def approve_return(
    payload: RefundApproveIn,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "orders.write")

    try:
        ret = (
            db.query(ReturnRequest)
            .filter(ReturnRequest.id == payload.return_id)
            .with_for_update()
            .first()
        )
        if not ret:
            raise HTTPException(status_code=404, detail="Return request not found")

        order = db.query(Order).filter(Order.id == ret.order_id).with_for_update().first()
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        if ret.status in _FINAL_RETURN_STATUSES and ret.provider_refund_id:
            return _refund_response(ret, "succeeded", idempotent=True)
        if order.payment_status not in {
            "paid",
            "refund_processing",
            "refund_pending",
            "partially_refunded",
        }:
            raise HTTPException(status_code=409, detail="Order is not eligible for refund")

        payment = (
            db.query(Payment)
            .filter(
                Payment.order_id == order.id,
                Payment.provider_payment_id != "",
                Payment.status.in_(["succeeded", "waiting_for_capture", "paid"]),
            )
            .order_by(Payment.created_at.desc(), Payment.id.desc())
            .with_for_update()
            .first()
        )
        if not payment:
            payment = (
                db.query(Payment)
                .filter(Payment.order_id == order.id, Payment.provider_payment_id != "")
                .order_by(Payment.created_at.desc(), Payment.id.desc())
                .with_for_update()
                .first()
            )
        if not payment:
            raise HTTPException(status_code=409, detail="No provider payment found for refund")

        requested_amount = _money(
            payload.amount if payload.amount is not None else (ret.refund_amount or order.total_amount),
            "refund amount",
        )
        order_total = _money(order.total_amount, "order total")
        if requested_amount <= 0 or requested_amount > order_total:
            raise HTTPException(status_code=409, detail="Refund amount must be positive and not exceed order total")
        if ret.refund_amount and _money(ret.refund_amount, "stored refund amount") != requested_amount:
            raise HTTPException(status_code=409, detail="Refund amount is already fixed for this request")

        ret.refund_amount = float(requested_amount)
        ret.status = "processing"
        order.status = "refund_requested"
        order.payment_status = "refund_processing"
        existing_refund_id = (ret.provider_refund_id or "").strip()
        payment_id = payment.provider_payment_id
        currency = order.currency
        order_id = order.id
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise

    try:
        if existing_refund_id:
            provider_refund = await fetch_yookassa_refund(existing_refund_id)
            data = {
                "refund_id": existing_refund_id,
                "status": str(provider_refund.get("status") or ""),
                "amount": provider_refund.get("amount") or {},
            }
        else:
            data = await create_yookassa_refund(
                payment_id,
                float(requested_amount),
                currency,
                order_id,
            )
    except HTTPException:
        try:
            ret = db.query(ReturnRequest).filter(ReturnRequest.id == payload.return_id).with_for_update().first()
            order = db.query(Order).filter(Order.id == order_id).with_for_update().first()
            if ret and ret.status == "processing":
                ret.status = "refund_retry_required"
            if order and order.payment_status == "refund_processing":
                order.payment_status = "refund_pending"
            db.commit()
        except Exception:
            db.rollback()
        raise

    provider_refund_id = str(data.get("refund_id") or "").strip()
    provider_status = str(data.get("status") or "").strip()
    if not provider_refund_id or not provider_status:
        raise HTTPException(status_code=502, detail="Payment provider returned an invalid refund")
    provider_amount = _provider_refund_amount(data, currency)
    if provider_amount != requested_amount:
        raise HTTPException(status_code=409, detail="Provider refund amount does not match approved amount")

    try:
        ret = (
            db.query(ReturnRequest)
            .filter(ReturnRequest.id == payload.return_id)
            .with_for_update()
            .first()
        )
        order = db.query(Order).filter(Order.id == order_id).with_for_update().first()
        if not ret or not order:
            raise HTTPException(status_code=409, detail="Refund state disappeared during processing")
        if ret.provider_refund_id and ret.provider_refund_id != provider_refund_id:
            raise HTTPException(status_code=409, detail="Return request is linked to another provider refund")
        if _money(ret.refund_amount, "stored refund amount") != requested_amount:
            raise HTTPException(status_code=409, detail="Stored refund amount changed during processing")

        ret.provider_refund_id = provider_refund_id
        _apply_provider_refund_status(db, ret, order, provider_status)
        log_admin_action(
            db,
            admin,
            "return.approve",
            "return",
            ret.id,
            {
                "order_id": order.id,
                "refund_id": provider_refund_id,
                "refund_amount": float(requested_amount),
                "provider_status": provider_status,
            },
        )
        db.commit()
        return _refund_response(ret, provider_status)
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Refund was already finalized") from exc
    except Exception:
        db.rollback()
        raise
