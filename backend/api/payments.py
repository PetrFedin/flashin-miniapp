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
from ..services.order_cancellation import cancel_order_before_settlement
from ..services.outbox import enqueue_webhook
from ..services.payment_attempts import (
    can_fallback_to_stored_attempt,
    is_stale_cancellation,
    resolve_provider_payment_attempt,
)
from ..services.payment_settlement import (
    SETTLED_ORDER_PAYMENT_STATUSES,
    settle_paid_order,
)
from ..services.payments import create_yookassa_payment, fetch_yookassa_payment
from ..services.pilot_circuit_breaker import (
    PilotCircuitBreakerError,
    stop_pilot_for_order,
    trip_pilot_circuit_breaker,
)

router = APIRouter(prefix="/payments", tags=["payments"])

_PROVIDER = "yookassa"
_RECONCILABLE_PAYMENT_STATUSES = {"pending", "waiting_for_capture", "succeeded"}
_PAYMENT_CREATION_ORDER_STATUSES = {"created", "payment_created"}
_PAYMENT_CREATION_PAYMENT_STATUSES = {"pending", "payment_created"}
_SUPPORTED_WEBHOOK_EVENTS = {
    "payment.waiting_for_capture",
    "payment.succeeded",
    "payment.canceled",
}
_MAX_WEBHOOK_BYTES = 64 * 1024


class ProviderPaymentIntegrityError(HTTPException):
    def __init__(
        self,
        reason: str,
        detail: str,
        *,
        status_code: int = 409,
    ):
        self.reason = reason
        super().__init__(status_code=status_code, detail=detail)


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
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ProviderPaymentIntegrityError(
            "provider_payment_amount_invalid",
            "Provider payment amount is invalid",
        ) from exc

    if not provider_amount.is_finite() or not order_amount.is_finite():
        raise ProviderPaymentIntegrityError(
            "provider_payment_amount_invalid",
            "Provider payment amount is invalid",
        )
    if provider_amount != order_amount or provider_currency != str(order.currency).upper():
        raise ProviderPaymentIntegrityError(
            "provider_payment_amount_or_currency_mismatch",
            "Provider payment amount or currency does not match order",
        )


def _trip_after_rollback(order_id: int, error: ProviderPaymentIntegrityError) -> HTTPException:
    try:
        trip_pilot_circuit_breaker(order_id=order_id, reason=error.reason)
    except PilotCircuitBreakerError as exc:
        return HTTPException(
            status_code=503,
            detail="Payment integrity failed and the pilot safety circuit could not be persisted",
        )
    return HTTPException(status_code=error.status_code, detail=error.detail)


async def _reconcile_existing_payment(order: Order, payment: Payment) -> Payment | None:
    provider_payment_id = str(payment.provider_payment_id or "").strip()
    if not provider_payment_id:
        payment.status = "invalid"
        payment.confirmation_url = ""
        return None

    try:
        provider_payment = await fetch_yookassa_payment(provider_payment_id)
    except HTTPException as exc:
        if exc.status_code == 502 and can_fallback_to_stored_attempt(
            payment.status,
            payment.confirmation_url,
        ):
            return payment
        raise

    if _provider_order_id(provider_payment) != order.id:
        raise ProviderPaymentIntegrityError(
            "provider_payment_order_reference_mismatch",
            "Provider payment belongs to another order",
        )
    _validate_provider_amount(provider_payment, order)

    resolution = resolve_provider_payment_attempt(
        provider_payment,
        stored_confirmation_url=payment.confirmation_url,
    )
    if resolution.outcome == "unavailable":
        raise ProviderPaymentIntegrityError(
            "provider_payment_confirmation_missing",
            "Payment is active but has no confirmation URL. Retry later or contact support.",
        )
    if resolution.outcome == "review":
        raise ProviderPaymentIntegrityError(
            "provider_payment_status_requires_review",
            f"Provider payment status requires review: {resolution.status}",
        )

    payment.status = resolution.status
    payment.confirmation_url = resolution.confirmation_url
    if resolution.outcome in {"reuse", "settled"}:
        return payment
    return None


def _parse_webhook_payload(raw_body: bytes) -> tuple[dict, str, dict, str]:
    if not raw_body:
        raise HTTPException(status_code=400, detail="Empty webhook payload")
    if len(raw_body) > _MAX_WEBHOOK_BYTES:
        raise HTTPException(status_code=413, detail="Webhook payload is too large")

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook payload must be an object")

    event = str(payload.get("event") or "").strip()
    if not event or len(event) > 120:
        raise HTTPException(status_code=400, detail="Invalid webhook event")

    raw_object = payload.get("object")
    obj = raw_object if isinstance(raw_object, dict) else {}
    payment_id = str(obj.get("id") or "").strip()
    if event in _SUPPORTED_WEBHOOK_EVENTS and (not payment_id or len(payment_id) > 255):
        raise HTTPException(status_code=400, detail="Invalid webhook payment id")
    return payload, event, obj, payment_id


def _queue_payment_review(db: Session, order: Order, payment_id: str, reason: str) -> None:
    stop_pilot_for_order(
        db,
        order_id=order.id,
        reason=f"payment_review:{reason}",
    )
    payload = {
        "order_id": order.id,
        "provider_payment_id": payment_id,
        "reason": reason,
    }
    emit_event(db, "payment.review_required", "order", order.id, payload)
    enqueue_webhook(
        db,
        "internal://payment-review-required",
        "payment.review_required",
        payload,
    )


@router.post("", response_model=PaymentOut)
async def create_payment(
    payload: PaymentCreate,
    customer=Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    provider_payment_id = ""
    order_id_for_integrity = payload.order_id
    try:
        order = (
            db.query(Order)
            .filter(Order.id == payload.order_id, Order.customer_id == customer.id)
            .with_for_update()
            .first()
        )
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        if order.status == "cancelled" or order.payment_status == "cancelled":
            raise HTTPException(status_code=409, detail="Order cancelled")
        if (
            order.status not in _PAYMENT_CREATION_ORDER_STATUSES
            or order.payment_status not in _PAYMENT_CREATION_PAYMENT_STATUSES
        ):
            raise HTTPException(status_code=409, detail="Order is not eligible for a new payment")
        if order.total_amount <= 0:
            raise HTTPException(status_code=409, detail="Order total must be positive")

        latest_payment = (
            db.query(Payment)
            .filter(Payment.order_id == order.id, Payment.provider == _PROVIDER)
            .order_by(Payment.id.desc())
            .with_for_update()
            .first()
        )
        if latest_payment and latest_payment.status in _RECONCILABLE_PAYMENT_STATUSES:
            reusable_payment = await _reconcile_existing_payment(order, latest_payment)
            if reusable_payment:
                if reusable_payment.status == "succeeded":
                    settle_paid_order(db, order)
                db.commit()
                return _payment_out(order, reusable_payment)

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
        if not provider_payment_id or len(provider_payment_id) > 255:
            raise ProviderPaymentIntegrityError(
                "provider_payment_id_invalid",
                "Payment provider returned an invalid payment id",
                status_code=502,
            )

        payment = Payment(
            order_id=order.id,
            provider=_PROVIDER,
            provider_payment_id=provider_payment_id,
            status=str(data.get("status") or "pending")[:64],
            amount=order.total_amount,
            confirmation_url=str(data.get("confirmation_url") or "")[:2048],
        )
        db.add(payment)
        order.payment_status = "payment_created"
        order.status = "payment_created"
        if payment.status == "succeeded":
            settle_paid_order(db, order)
        db.commit()
        return _payment_out(order, payment)
    except ProviderPaymentIntegrityError as exc:
        db.rollback()
        raise _trip_after_rollback(order_id_for_integrity, exc)
    except PilotCircuitBreakerError as exc:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail="Pilot safety circuit could not be applied",
        ) from exc
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
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type and content_type != "application/json":
        raise HTTPException(status_code=415, detail="Webhook content type must be application/json")

    raw_content_length = request.headers.get("content-length", "").strip()
    if raw_content_length:
        try:
            if int(raw_content_length) > _MAX_WEBHOOK_BYTES:
                raise HTTPException(status_code=413, detail="Webhook payload is too large")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length header")

    payload, event, obj, payment_id = _parse_webhook_payload(await request.body())
    if event not in _SUPPORTED_WEBHOOK_EVENTS:
        return {"ok": True, "ignored": True, "event": event}

    provider_payment = await fetch_yookassa_payment(payment_id)
    provider_order_id = _provider_order_id(provider_payment)

    try:
        provider_status = str(provider_payment.get("status") or obj.get("status") or "").strip()
        if not provider_status or len(provider_status) > 64:
            raise ProviderPaymentIntegrityError(
                "provider_payment_status_invalid",
                "Provider payment has no valid status",
            )

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
            raise ProviderPaymentIntegrityError(
                "stored_payment_order_mismatch",
                "Payment belongs to another order",
            )

        latest_order_payment = (
            db.query(Payment)
            .filter(Payment.order_id == order.id, Payment.provider == _PROVIDER)
            .order_by(Payment.id.desc())
            .with_for_update()
            .first()
        )

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
                confirmation_url=str((provider_payment.get("confirmation") or {}).get("confirmation_url") or "")[:2048],
            )
            db.add(payment)
        else:
            payment.status = provider_status

        if event == "payment.succeeded" and provider_status == "succeeded":
            if order.status == "cancelled" or order.payment_status == "cancelled":
                order.payment_status = "paid_review_required"
                order.status = "payment_review_required"
                _queue_payment_review(db, order, payment_id, "paid_after_cancel")
            else:
                settle_paid_order(db, order)
        elif event == "payment.canceled" and provider_status == "canceled":
            if latest_order_payment and is_stale_cancellation(
                payment_id,
                latest_order_payment.provider_payment_id,
                latest_order_payment.status,
            ):
                emit_event(
                    db,
                    "payment.cancellation_ignored",
                    "order",
                    order.id,
                    {
                        "order_id": order.id,
                        "canceled_payment_id": payment_id,
                        "latest_payment_id": latest_order_payment.provider_payment_id,
                        "latest_payment_status": latest_order_payment.status,
                    },
                )
            elif order.payment_status in SETTLED_ORDER_PAYMENT_STATUSES:
                _queue_payment_review(db, order, payment_id, "canceled_after_settlement")
            else:
                try:
                    cancel_order_before_settlement(db, order, source="provider")
                except HTTPException as exc:
                    order.payment_status = "payment_review_required"
                    order.status = "payment_review_required"
                    _queue_payment_review(
                        db,
                        order,
                        payment_id,
                        f"provider_cancel_conflict:{exc.detail}",
                    )

        payment_event.processed = True
        db.commit()
        return {"ok": True}
    except ProviderPaymentIntegrityError as exc:
        db.rollback()
        raise _trip_after_rollback(provider_order_id, exc)
    except PilotCircuitBreakerError as exc:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail="Pilot safety circuit could not be applied",
        ) from exc
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
