import json
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Order, Payment, PaymentEvent
from ..schemas import PaymentCreate, PaymentOut
from ..security import get_current_customer
from ..services.event_dispatcher import emit_event
from ..services.fulfillment import ensure_fulfillment_task
from ..services.inventory import commit_reserved_to_sold, release_variant
from ..services.loyalty import add_points, mark_redemption_committed, reward_referral_after_first_paid_order
from ..services.notifications import queue_order_paid
from ..services.outbox import enqueue_event_for_destinations, enqueue_webhook
from ..services.payments import create_yookassa_payment, fetch_yookassa_payment
from ..services.timeline import add_timeline_event

router = APIRouter(prefix="/payments", tags=["payments"])

_PROVIDER = "yookassa"
_REUSABLE_PAYMENT_STATUSES = {"pending", "waiting_for_capture", "succeeded"}


def _payment_out(order: Order, payment: Payment) -> PaymentOut:
    return PaymentOut(
        order_id=order.id,
        provider=payment.provider,
        status=payment.status,
        confirmation_url=payment.confirmation_url,
        provider_payment_id=payment.provider_payment_id,
    )


def _provider_order_id(provider_payment: dict) -> int:
    raw_order_id = (provider_payment.get("metadata") or {}).get("order_id")
    try:
        order_id = int(raw_order_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=409, detail="Provider payment has no valid order reference")
    if order_id < 1:
        raise HTTPException(status_code=409, detail="Provider payment has no valid order reference")
    return order_id


def _validate_provider_amount(provider_payment: dict, order: Order) -> None:
    amount = provider_payment.get("amount") or {}
    provider_currency = str(amount.get("currency") or "").upper()
    try:
        provider_amount = Decimal(str(amount.get("value"))).quantize(Decimal("0.01"))
        order_amount = Decimal(str(order.total_amount)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(status_code=409, detail="Provider payment amount is invalid")

    if provider_amount != order_amount or provider_currency != str(order.currency).upper():
        raise HTTPException(status_code=409, detail="Provider payment amount or currency does not match order")


@router.post("", response_model=PaymentOut)
async def create_payment(
    payload: PaymentCreate,
    customer=Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    provider_payment_id = ""
    try:
        order = (
            db.query(Order)
            .filter(Order.id == payload.order_id, Order.customer_id == customer.id)
            .with_for_update()
            .first()
        )
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        if order.payment_status == "paid":
            raise HTTPException(status_code=409, detail="Order already paid")
        if order.status == "cancelled":
            raise HTTPException(status_code=409, detail="Order cancelled")
        if order.total_amount <= 0:
            raise HTTPException(status_code=409, detail="Order total must be positive")

        latest_payment = (
            db.query(Payment)
            .filter(Payment.order_id == order.id, Payment.provider == _PROVIDER)
            .order_by(Payment.id.desc())
            .with_for_update()
            .first()
        )
        if latest_payment and latest_payment.status in _REUSABLE_PAYMENT_STATUSES:
            return _payment_out(order, latest_payment)

        attempt = (
            db.query(func.count(Payment.id))
            .filter(Payment.order_id == order.id, Payment.provider == _PROVIDER)
            .scalar()
            or 0
        ) + 1
        data = await create_yookassa_payment(
            order.id,
            order.total_amount,
            order.currency,
            attempt=attempt,
        )
        provider_payment_id = str(data.get("provider_payment_id") or "").strip()
        if not provider_payment_id:
            raise HTTPException(status_code=502, detail="Payment provider returned no payment id")

        payment = Payment(
            order_id=order.id,
            provider=_PROVIDER,
            provider_payment_id=provider_payment_id,
            status=str(data.get("status") or "pending"),
            amount=order.total_amount,
            confirmation_url=str(data.get("confirmation_url") or ""),
        )
        db.add(payment)
        order.payment_status = "payment_created"
        order.status = "payment_created"
        db.commit()
        return _payment_out(order, payment)
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        if provider_payment_id:
            existing = (
                db.query(Payment)
                .filter(Payment.provider == _PROVIDER, Payment.provider_payment_id == provider_payment_id)
                .first()
            )
            if existing and existing.order_id == payload.order_id:
                order = db.query(Order).filter(Order.id == payload.order_id).first()
                if order:
                    return _payment_out(order, existing)
        raise HTTPException(status_code=409, detail="Payment request was already created")
    except Exception:
        db.rollback()
        raise


@router.post("/webhook/yookassa")
async def yookassa_webhook(request: Request, db: Session = Depends(get_db)):
    try:
        payload = await request.json()
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event = str(payload.get("event") or "").strip()
    obj = payload.get("object") or {}
    payment_id = str(obj.get("id") or "").strip()
    if not payment_id or not event:
        raise HTTPException(status_code=400, detail="Invalid webhook payload")

    provider_payment = await fetch_yookassa_payment(payment_id)
    provider_order_id = _provider_order_id(provider_payment)
    provider_status = str(provider_payment.get("status") or obj.get("status") or "").strip()
    if not provider_status:
        raise HTTPException(status_code=409, detail="Provider payment has no status")

    try:
        order = db.query(Order).filter(Order.id == provider_order_id).with_for_update().first()
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        _validate_provider_amount(provider_payment, order)

        payment = (
            db.query(Payment)
            .filter(Payment.provider == _PROVIDER, Payment.provider_payment_id == payment_id)
            .with_for_update()
            .first()
        )
        if payment and payment.order_id != order.id:
            raise HTTPException(status_code=409, detail="Payment belongs to another order")

        payment_event = (
            db.query(PaymentEvent)
            .filter(
                PaymentEvent.provider == _PROVIDER,
                PaymentEvent.provider_payment_id == payment_id,
                PaymentEvent.event_type == event,
            )
            .with_for_update()
            .first()
        )
        if payment_event and payment_event.processed:
            return {"ok": True, "idempotent": True}

        if not payment_event:
            payment_event = PaymentEvent(
                provider=_PROVIDER,
                provider_payment_id=payment_id,
                event_type=event,
                raw_payload=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                processed=False,
            )
            db.add(payment_event)

        if not payment:
            payment = Payment(
                order_id=order.id,
                provider=_PROVIDER,
                provider_payment_id=payment_id,
                status=provider_status,
                amount=order.total_amount,
                confirmation_url=str((provider_payment.get("confirmation") or {}).get("confirmation_url") or ""),
            )
            db.add(payment)
        else:
            payment.status = provider_status

        if event == "payment.succeeded" and provider_status == "succeeded":
            if order.payment_status == "paid":
                pass
            elif order.status == "cancelled" or order.payment_status == "cancelled":
                order.payment_status = "paid_review_required"
                order.status = "payment_review_required"
                emit_event(
                    db,
                    "payment.review_required",
                    "order",
                    order.id,
                    {"order_id": order.id, "provider_payment_id": payment_id, "reason": "paid_after_cancel"},
                )
                enqueue_webhook(
                    db,
                    "internal://payment-review-required",
                    "payment.review_required",
                    {"order_id": order.id, "provider_payment_id": payment_id, "reason": "paid_after_cancel"},
                )
            else:
                for item in order.items:
                    commit_reserved_to_sold(db, item.variant_id, item.quantity)
                queue_order_paid(db, order)
                if order.loyalty_points_redeemed:
                    add_points(db, order.customer_id, -order.loyalty_points_redeemed, "loyalty_redeemed", order.id)
                    mark_redemption_committed(
                        db,
                        order.customer_id,
                        None,
                        order.id,
                        order.loyalty_points_redeemed,
                    )
                add_points(db, order.customer_id, round(order.total_amount * 0.01, 2), "order_paid", order.id)
                reward_referral_after_first_paid_order(db, order.customer_id, order.id)
                add_timeline_event(
                    db,
                    order.customer_id,
                    "order_paid",
                    f"Заказ #{order.id} оплачен",
                    {"total": order.total_amount},
                )
                ensure_fulfillment_task(db, order)
                emit_event(db, "order.paid", "order", order.id, {"order_id": order.id, "total": order.total_amount})
                enqueue_webhook(
                    db,
                    "internal://order-paid",
                    "order.paid",
                    {"order_id": order.id, "total": order.total_amount},
                )
                enqueue_event_for_destinations(
                    db,
                    "order.paid",
                    {"order_id": order.id, "total": order.total_amount},
                )
                order.payment_status = "paid"
                order.status = "paid"
        elif event == "payment.canceled" and provider_status == "canceled":
            if order.payment_status != "paid":
                if order.payment_status != "cancelled":
                    for item in order.items:
                        release_variant(db, item.variant_id, item.quantity)
                order.payment_status = "cancelled"
                order.status = "cancelled"

        payment_event.processed = True
        db.commit()
        return {"ok": True}
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        existing_event = (
            db.query(PaymentEvent)
            .filter(
                PaymentEvent.provider == _PROVIDER,
                PaymentEvent.provider_payment_id == payment_id,
                PaymentEvent.event_type == event,
                PaymentEvent.processed.is_(True),
            )
            .first()
        )
        if existing_event:
            return {"ok": True, "idempotent": True}
        raise HTTPException(status_code=409, detail="Webhook event is already being processed")
    except Exception:
        db.rollback()
        raise
