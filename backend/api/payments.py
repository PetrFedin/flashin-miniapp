import json
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session, joinedload
from ..database import get_db
from ..models import Order, Payment, PaymentEvent
from ..schemas import PaymentCreate, PaymentOut
from ..security import get_current_customer
from ..services.inventory import commit_reserved_to_sold, release_variant
from ..services.notifications import queue_order_paid
from ..services.payments import create_yookassa_payment, fetch_yookassa_payment
from ..services.outbox import enqueue_event_for_destinations, enqueue_webhook
from ..services.fulfillment import ensure_fulfillment_task
from ..services.event_dispatcher import emit_event
from ..services.loyalty import add_points, reward_referral_after_first_paid_order, mark_redemption_committed
from ..services.timeline import add_timeline_event

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("", response_model=PaymentOut)
async def create_payment(payload: PaymentCreate, customer=Depends(get_current_customer), db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == payload.order_id, Order.customer_id == customer.id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.payment_status == "paid":
        raise HTTPException(status_code=409, detail="Order already paid")
    if order.status == "cancelled":
        raise HTTPException(status_code=409, detail="Order cancelled")

    data = await create_yookassa_payment(order.id, order.total_amount, order.currency)
    payment = Payment(
        order_id=order.id,
        provider="yookassa",
        provider_payment_id=data["provider_payment_id"],
        status=data["status"],
        amount=order.total_amount,
        confirmation_url=data["confirmation_url"],
    )
    db.add(payment)
    order.payment_status = "payment_created"
    order.status = "payment_created"
    db.commit()

    return PaymentOut(
        order_id=order.id,
        provider="yookassa",
        status=payment.status,
        confirmation_url=payment.confirmation_url,
        provider_payment_id=payment.provider_payment_id,
    )


@router.post("/webhook/yookassa")
async def yookassa_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    event = payload.get("event")
    obj = payload.get("object", {})
    payment_id = obj.get("id")
    metadata = obj.get("metadata") or {}
    order_id = metadata.get("order_id")

    if not payment_id or not order_id or not event:
        raise HTTPException(status_code=400, detail="Invalid webhook payload")

    existing_event = (
        db.query(PaymentEvent)
        .filter(PaymentEvent.provider == "yookassa", PaymentEvent.provider_payment_id == payment_id, PaymentEvent.event_type == event)
        .first()
    )
    if existing_event and existing_event.processed:
        return {"ok": True, "idempotent": True}

    payment_event = existing_event or PaymentEvent(
        provider="yookassa",
        provider_payment_id=payment_id,
        event_type=event,
        raw_payload=json.dumps(payload, ensure_ascii=False),
        processed=False,
    )
    if not existing_event:
        db.add(payment_event)

    payment = db.query(Payment).filter(Payment.provider_payment_id == payment_id).first()
    order = db.query(Order).options(joinedload(Order.items), joinedload(Order.customer)).filter(Order.id == int(order_id)).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if not payment:
        payment = Payment(order_id=order.id, provider="yookassa", provider_payment_id=payment_id, status=obj.get("status", ""), amount=order.total_amount)
        db.add(payment)

    provider_payment = await fetch_yookassa_payment(payment_id)
    provider_status = provider_payment.get("status", obj.get("status"))
    payment.status = provider_status

    if event == "payment.succeeded" and provider_status == "succeeded":
        if order.payment_status != "paid":
            for item in order.items:
                commit_reserved_to_sold(db, item.variant_id, item.quantity)
            queue_order_paid(db, order)
            if order.loyalty_points_redeemed:
                add_points(db, order.customer_id, -order.loyalty_points_redeemed, "loyalty_redeemed", order.id)
                mark_redemption_committed(db, order.customer_id, None, order.id, order.loyalty_points_redeemed)
            add_points(db, order.customer_id, round(order.total_amount * 0.01, 2), "order_paid", order.id)
            reward_referral_after_first_paid_order(db, order.customer_id, order.id)
            add_timeline_event(db, order.customer_id, "order_paid", f"Заказ #{order.id} оплачен", {"total": order.total_amount})
            ensure_fulfillment_task(db, order)
            emit_event(db, "order.paid", "order", order.id, {"order_id": order.id, "total": order.total_amount})
            enqueue_webhook(db, "internal://order-paid", "order.paid", {"order_id": order.id, "total": order.total_amount})
            enqueue_event_for_destinations(db, "order.paid", {"order_id": order.id, "total": order.total_amount})
        order.payment_status = "paid"
        order.status = "paid"
    elif event == "payment.canceled" and provider_status == "canceled":
        if order.payment_status != "cancelled":
            for item in order.items:
                release_variant(db, item.variant_id, item.quantity)
        order.payment_status = "cancelled"
        order.status = "cancelled"

    payment_event.processed = True
    db.commit()
    return {"ok": True}
