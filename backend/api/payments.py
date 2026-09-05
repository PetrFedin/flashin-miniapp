import json
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Request
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
from ..services.payment_creation import (
    begin_payment_creation,
    complete_payment_creation_from_provider,
    finalize_payment_creation,
    load_claim_payment,
    mark_payment_creation_retry_required,
    mark_payment_creation_review_required,
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
from ..services.provider_failures import is_retryable_yookassa_error, yookassa_error_reason

router = APIRouter(prefix="/payments", tags=["payments"])

_PROVIDER = "yookassa"
_RECONCILABLE_PAYMENT_STATUSES = {"pending", "waiting_for_capture", "succeeded"}
_SUPPORTED_WEBHOOK_EVENTS = {
    "payment.waiting_for_capture",
    "payment.succeeded",
    "payment.canceled",
}
_REVIEW_PAYMENT_STATUSES = {"payment_review_required", "paid_review_required"}
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


def _provider_payment_id(provider_payment: dict) -> str:
    payment_id = str(provider_payment.get("id") or "").strip()
    if not payment_id or len(payment_id) > 255:
        raise ProviderPaymentIntegrityError(
            "provider_payment_id_invalid",
            "Payment provider returned an invalid payment id",
            status_code=502,
        )
    return payment_id


def _provider_payment_status(provider_payment: dict) -> str:
    status = str(provider_payment.get("status") or "").strip().lower()
    if not status or len(status) > 64:
        raise ProviderPaymentIntegrityError(
            "provider_payment_status_invalid",
            "Payment provider returned an invalid payment status",
            status_code=502,
        )
    return status


def _validate_provider_amount_values(
    provider_payment: dict,
    expected_amount: float,
    expected_currency: str,
) -> None:
    amount = provider_payment.get("amount") or {}
    provider_currency = str(amount.get("currency") or "").upper()
    try:
        provider_amount = Decimal(str(amount.get("value"))).quantize(Decimal("0.01"))
        expected = Decimal(str(expected_amount)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ProviderPaymentIntegrityError(
            "provider_payment_amount_invalid",
            "Provider payment amount is invalid",
        ) from exc

    if not provider_amount.is_finite() or not expected.is_finite():
        raise ProviderPaymentIntegrityError(
            "provider_payment_amount_invalid",
            "Provider payment amount is invalid",
        )
    if provider_amount != expected or provider_currency != str(expected_currency).upper():
        raise ProviderPaymentIntegrityError(
            "provider_payment_amount_or_currency_mismatch",
            "Provider payment amount or currency does not match order",
        )


def _validate_provider_amount(provider_payment: dict, order: Order) -> None:
    _validate_provider_amount_values(provider_payment, order.total_amount, order.currency)


def _validate_created_provider_payment(
    provider_payment: dict,
    *,
    order_id: int,
    amount: float,
    currency: str,
    stored_confirmation_url: str = "",
) -> tuple[str, str, str]:
    provider_payment_id = _provider_payment_id(provider_payment)
    _provider_payment_status(provider_payment)
    try:
        provider_order_id = _provider_order_id(provider_payment)
    except HTTPException as exc:
        raise ProviderPaymentIntegrityError(
            "provider_payment_order_reference_invalid",
            "Provider payment has no valid order reference",
            status_code=502,
        ) from exc
    if provider_order_id != order_id:
        raise ProviderPaymentIntegrityError(
            "provider_payment_order_reference_mismatch",
            "Provider payment belongs to another order",
        )
    _validate_provider_amount_values(provider_payment, amount, currency)

    resolution = resolve_provider_payment_attempt(
        provider_payment,
        stored_confirmation_url=stored_confirmation_url,
    )
    if resolution.outcome == "unavailable":
        raise ProviderPaymentIntegrityError(
            "provider_payment_confirmation_missing",
            "Payment is active but has no confirmation URL",
            status_code=502,
        )
    if resolution.outcome == "review":
        raise ProviderPaymentIntegrityError(
            "provider_payment_status_requires_review",
            f"Provider payment status requires review: {resolution.status}",
            status_code=502,
        )
    return provider_payment_id, resolution.status, resolution.confirmation_url


def _trip_after_rollback(order_id: int, error: ProviderPaymentIntegrityError) -> HTTPException:
    try:
        trip_pilot_circuit_breaker(order_id=order_id, reason=error.reason)
    except PilotCircuitBreakerError as exc:
        return HTTPException(
            status_code=503,
            detail="Payment integrity failed and the pilot safety circuit could not be persisted",
        )
    return HTTPException(status_code=error.status_code, detail=error.detail)


def _apply_provider_reconciliation(
    order: Order,
    payment: Payment,
    provider_payment: dict,
) -> Payment | None:
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


def _persist_creation_review(
    db: Session,
    *,
    attempt_id: int,
    order_id: int,
    provider_payment_id: str,
    provider_status: str,
    reason: str,
) -> None:
    mark_payment_creation_review_required(
        db,
        attempt_id,
        reason,
        provider_payment_id=provider_payment_id,
    )
    order = db.query(Order).filter(Order.id == order_id).with_for_update().first()
    if order:
        paid_like = provider_status == "succeeded" or order.payment_status in SETTLED_ORDER_PAYMENT_STATUSES
        order.status = "payment_review_required"
        order.payment_status = "paid_review_required" if paid_like else "payment_review_required"
        _queue_payment_review(db, order, provider_payment_id, reason)
    db.commit()


def _persist_creation_review_fail_closed(
    db: Session,
    *,
    attempt_id: int,
    order_id: int,
    provider_payment_id: str,
    provider_status: str,
    reason: str,
) -> None:
    try:
        _persist_creation_review(
            db,
            attempt_id=attempt_id,
            order_id=order_id,
            provider_payment_id=provider_payment_id,
            provider_status=provider_status,
            reason=reason,
        )
    except Exception as exc:
        db.rollback()
        try:
            trip_pilot_circuit_breaker(order_id=order_id, reason=f"payment_review_persist_failed:{reason}")
        except PilotCircuitBreakerError as circuit_exc:
            raise HTTPException(
                status_code=503,
                detail="Payment state is uncertain and the pilot safety circuit could not be persisted",
            ) from circuit_exc
        raise HTTPException(
            status_code=503,
            detail="Payment state is uncertain; the pilot was stopped for manual reconciliation",
        ) from exc


async def _reconcile_claimed_existing_payment(
    db: Session,
    claim,
) -> PaymentOut | None:
    order, payment = load_claim_payment(db, claim)
    payment_id = str(payment.provider_payment_id or "").strip()
    stored_status = str(payment.status or "")
    stored_confirmation_url = str(payment.confirmation_url or "")
    order_id = order.id
    payment_db_id = payment.id
    db.commit()

    if not payment_id:
        error = ProviderPaymentIntegrityError(
            "provider_payment_id_invalid",
            "Stored payment has no provider payment id",
        )
        raise _trip_after_rollback(order_id, error)

    try:
        provider_payment = await fetch_yookassa_payment(payment_id)
    except HTTPException as exc:
        if exc.status_code == 502 and can_fallback_to_stored_attempt(
            stored_status,
            stored_confirmation_url,
        ):
            order = db.query(Order).filter(Order.id == order_id).first()
            payment = db.query(Payment).filter(Payment.id == payment_db_id).first()
            if order and payment:
                return _payment_out(order, payment)
        raise

    try:
        order = db.query(Order).filter(Order.id == order_id).with_for_update().first()
        payment = db.query(Payment).filter(Payment.id == payment_db_id).with_for_update().first()
        if not order or not payment or payment.order_id != order.id:
            raise ProviderPaymentIntegrityError(
                "stored_payment_order_mismatch",
                "Stored payment no longer matches the order",
            )
        reusable_payment = _apply_provider_reconciliation(order, payment, provider_payment)
        if reusable_payment:
            if reusable_payment.status == "succeeded":
                settle_paid_order(db, order)
            complete_payment_creation_from_provider(db, order.id, reusable_payment.provider_payment_id)
            db.commit()
            return _payment_out(order, reusable_payment)
        db.commit()
        return None
    except ProviderPaymentIntegrityError as exc:
        db.rollback()
        raise _trip_after_rollback(order_id, exc)


@router.post("", response_model=PaymentOut)
async def create_payment(
    payload: PaymentCreate,
    customer=Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    provider_payment_id = ""
    provider_status = ""
    attempt_id: int | None = None
    order_id = payload.order_id

    try:
        claim = begin_payment_creation(db, payload.order_id, customer.id)
        if claim.is_existing:
            existing_result = await _reconcile_claimed_existing_payment(db, claim)
            if existing_result is not None:
                return existing_result
            claim = begin_payment_creation(db, payload.order_id, customer.id)

        if not claim.attempt_id or not claim.attempt_number:
            raise HTTPException(status_code=500, detail="Payment creation claim is incomplete")

        attempt_id = claim.attempt_id
        attempt_number = claim.attempt_number
        amount = claim.amount
        currency = claim.currency
        order_id = claim.order_id

        # Persist the claim and release all order/payment row locks before the provider side effect.
        db.commit()

        try:
            data = await create_yookassa_payment(
                order_id,
                amount,
                currency,
                attempt=attempt_number,
            )
        except HTTPException as exc:
            reason = yookassa_error_reason(exc)
            db.rollback()
            if is_retryable_yookassa_error(exc):
                mark_payment_creation_retry_required(db, attempt_id, reason)
                db.commit()
                raise
            _persist_creation_review_fail_closed(
                db,
                attempt_id=attempt_id,
                order_id=order_id,
                provider_payment_id="",
                provider_status="",
                reason=reason,
            )
            raise HTTPException(
                status_code=503,
                detail="Payment provider rejected the request; manual review is required",
            ) from exc
        except Exception as exc:
            db.rollback()
            reason = yookassa_error_reason(exc)
            _persist_creation_review_fail_closed(
                db,
                attempt_id=attempt_id,
                order_id=order_id,
                provider_payment_id="",
                provider_status="",
                reason=reason,
            )
            raise HTTPException(
                status_code=503,
                detail="Payment provider response is uncertain; manual review is required",
            ) from exc

        provider_payment_id = str(data.get("provider_payment_id") or "").strip()
        provider_status = str(data.get("status") or "").strip().lower()
        confirmation_url = str(data.get("confirmation_url") or "").strip()[:2048]
        if not provider_payment_id or len(provider_payment_id) > 255:
            db.rollback()
            _persist_creation_review_fail_closed(
                db,
                attempt_id=attempt_id,
                order_id=order_id,
                provider_payment_id="",
                provider_status=provider_status,
                reason="provider_payment_id_invalid",
            )
            raise HTTPException(
                status_code=503,
                detail="Payment provider returned an invalid payment id; manual review is required",
            )

        try:
            provider_payment = await fetch_yookassa_payment(provider_payment_id)
            validated_payment_id, validated_status, validated_confirmation_url = _validate_created_provider_payment(
                provider_payment,
                order_id=order_id,
                amount=amount,
                currency=currency,
                stored_confirmation_url=confirmation_url,
            )
            if provider_payment_id != validated_payment_id:
                raise ProviderPaymentIntegrityError(
                    "provider_payment_id_mismatch",
                    "Payment provider returned inconsistent payment ids",
                    status_code=502,
                )
            if provider_status and provider_status != validated_status:
                raise ProviderPaymentIntegrityError(
                    "provider_payment_status_mismatch",
                    "Payment provider returned inconsistent payment statuses",
                    status_code=502,
                )
            provider_status = validated_status
            confirmation_url = validated_confirmation_url
        except ProviderPaymentIntegrityError as exc:
            db.rollback()
            _persist_creation_review_fail_closed(
                db,
                attempt_id=attempt_id,
                order_id=order_id,
                provider_payment_id=provider_payment_id,
                provider_status=provider_status,
                reason=exc.reason,
            )
            raise HTTPException(
                status_code=503,
                detail="Payment provider response failed integrity validation; manual review is required",
            ) from exc
        except HTTPException as exc:
            db.rollback()
            _persist_creation_review_fail_closed(
                db,
                attempt_id=attempt_id,
                order_id=order_id,
                provider_payment_id=provider_payment_id,
                provider_status=provider_status,
                reason=f"provider_payment_verification_failed:{exc.status_code}",
            )
            raise HTTPException(
                status_code=503,
                detail="Payment was created but could not be verified; manual review is required",
            ) from exc

        try:
            order, payment = finalize_payment_creation(
                db,
                attempt_id,
                provider_payment_id,
                provider_status,
                confirmation_url,
            )
            review_required = (
                order.status == "payment_review_required"
                or order.payment_status in _REVIEW_PAYMENT_STATUSES
            )
            if review_required:
                stop_pilot_for_order(
                    db,
                    order_id=order.id,
                    reason="payment_review:payment_creation_finalize_review",
                )
            elif payment.status == "succeeded":
                settle_paid_order(db, order)
            db.commit()
            if review_required:
                raise HTTPException(
                    status_code=503,
                    detail="Payment was created but requires manual reconciliation",
                )
            return _payment_out(order, payment)
        except IntegrityError:
            db.rollback()
            existing = (
                db.query(Payment)
                .filter(Payment.provider == _PROVIDER, Payment.provider_payment_id == provider_payment_id)
                .first()
            )
            if existing and existing.order_id == order_id:
                order = db.query(Order).filter(Order.id == order_id).with_for_update().first()
                if order:
                    complete_payment_creation_from_provider(db, order_id, provider_payment_id)
                    if existing.status == "succeeded":
                        settle_paid_order(db, order)
                    db.commit()
                    return _payment_out(order, existing)
            _persist_creation_review_fail_closed(
                db,
                attempt_id=attempt_id,
                order_id=order_id,
                provider_payment_id=provider_payment_id,
                provider_status=provider_status,
                reason="payment_finalize_integrity_error",
            )
            raise HTTPException(
                status_code=503,
                detail="Payment creation requires manual reconciliation",
            )
        except HTTPException as exc:
            db.rollback()
            if exc.status_code == 503 and "manual reconciliation" in str(exc.detail).lower():
                raise
            _persist_creation_review_fail_closed(
                db,
                attempt_id=attempt_id,
                order_id=order_id,
                provider_payment_id=provider_payment_id,
                provider_status=provider_status,
                reason=f"payment_finalize_failed:{exc.status_code}",
            )
            raise HTTPException(
                status_code=503,
                detail="Payment creation requires manual reconciliation",
            ) from exc
        except Exception as exc:
            db.rollback()
            _persist_creation_review_fail_closed(
                db,
                attempt_id=attempt_id,
                order_id=order_id,
                provider_payment_id=provider_payment_id,
                provider_status=provider_status,
                reason=f"payment_finalize_failed:{exc.__class__.__name__}",
            )
            raise HTTPException(
                status_code=503,
                detail="Payment creation requires manual reconciliation",
            ) from exc
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
        raise HTTPException(status_code=409, detail="Payment request is already being processed")
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

        complete_payment_creation_from_provider(db, order.id, payment_id)

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
