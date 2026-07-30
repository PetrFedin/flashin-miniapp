from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import Order, Payment
from ..payment_attempt_models import PaymentCreationAttempt
from ..payment_attempt_statuses import (
    ABANDONED_PAYMENT_ATTEMPT_STATUS,
    COMPLETED_PAYMENT_ATTEMPT_STATUS,
    CREATING_PAYMENT_ATTEMPT_STATUS,
    RETRYABLE_PAYMENT_ATTEMPT_STATUSES,
    RETRY_REQUIRED_PAYMENT_ATTEMPT_STATUS,
    REVIEW_REQUIRED_PAYMENT_ATTEMPT_STATUS,
)
from .event_dispatcher import emit_event
from .outbox import enqueue_webhook

_PROVIDER = "yookassa"
_REUSABLE_PAYMENT_STATUSES = {"pending", "waiting_for_capture", "succeeded"}
_ELIGIBLE_ORDER_STATUSES = {"created", "payment_created"}
_ELIGIBLE_PAYMENT_STATUSES = {"pending", "payment_created"}
_SETTLED_ORDER_PAYMENT_STATUSES = {
    "paid",
    "paid_review_required",
    "refund_processing",
    "refund_retry_required",
    "refund_pending",
    "refund_review_required",
    "partially_refunded",
    "refunded",
}
_DEFAULT_LEASE_SECONDS = 120


@dataclass(frozen=True)
class PaymentCreationClaim:
    order_id: int
    amount: float
    currency: str
    attempt_id: int | None = None
    existing_payment_id: int | None = None

    @property
    def is_existing(self) -> bool:
        return self.existing_payment_id is not None


def _payment_for_output(db: Session, payment_id: int) -> tuple[Order, Payment]:
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=409, detail="Payment creation result is missing")
    order = db.query(Order).filter(Order.id == payment.order_id).first()
    if not order:
        raise HTTPException(status_code=409, detail="Payment order is missing")
    return order, payment


def load_claim_payment(db: Session, claim: PaymentCreationClaim) -> tuple[Order, Payment]:
    if not claim.existing_payment_id:
        raise HTTPException(status_code=409, detail="Payment creation is not finalized")
    return _payment_for_output(db, claim.existing_payment_id)


def begin_payment_creation(
    db: Session,
    order_id: int,
    customer_id: int,
    *,
    lease_seconds: int = _DEFAULT_LEASE_SECONDS,
) -> PaymentCreationClaim:
    if not 30 <= lease_seconds <= 600:
        raise HTTPException(status_code=500, detail="Payment creation lease is misconfigured")

    order = (
        db.query(Order)
        .filter(Order.id == order_id, Order.customer_id == customer_id)
        .with_for_update()
        .first()
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.status == "cancelled" or order.payment_status == "cancelled":
        raise HTTPException(status_code=409, detail="Order cancelled")
    if (
        order.status not in _ELIGIBLE_ORDER_STATUSES
        or order.payment_status not in _ELIGIBLE_PAYMENT_STATUSES
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
    if latest_payment and latest_payment.status in _REUSABLE_PAYMENT_STATUSES:
        return PaymentCreationClaim(
            order_id=order.id,
            amount=order.total_amount,
            currency=order.currency,
            existing_payment_id=latest_payment.id,
        )

    latest_attempt = (
        db.query(PaymentCreationAttempt)
        .filter(
            PaymentCreationAttempt.order_id == order.id,
            PaymentCreationAttempt.provider == _PROVIDER,
        )
        .order_by(PaymentCreationAttempt.attempt_number.desc(), PaymentCreationAttempt.id.desc())
        .with_for_update()
        .first()
    )

    if (
        latest_attempt
        and latest_attempt.status == COMPLETED_PAYMENT_ATTEMPT_STATUS
        and latest_attempt.provider_payment_id
    ):
        completed_payment = (
            db.query(Payment)
            .filter(
                Payment.provider == _PROVIDER,
                Payment.provider_payment_id == latest_attempt.provider_payment_id,
            )
            .first()
        )
        if completed_payment:
            return PaymentCreationClaim(
                order_id=order.id,
                amount=order.total_amount,
                currency=order.currency,
                existing_payment_id=completed_payment.id,
            )
        raise HTTPException(status_code=409, detail="Completed payment attempt requires reconciliation")

    if latest_attempt and latest_attempt.status == REVIEW_REQUIRED_PAYMENT_ATTEMPT_STATUS:
        raise HTTPException(
            status_code=409,
            detail="Payment creation attempt requires manual review before retry",
        )

    now = datetime.utcnow()
    lease_expires_at = now + timedelta(seconds=lease_seconds)
    if latest_attempt and latest_attempt.status in RETRYABLE_PAYMENT_ATTEMPT_STATUSES:
        if (
            latest_attempt.status == CREATING_PAYMENT_ATTEMPT_STATUS
            and latest_attempt.lease_expires_at
            and latest_attempt.lease_expires_at > now
        ):
            retry_after = max(int((latest_attempt.lease_expires_at - now).total_seconds()) + 1, 1)
            raise HTTPException(
                status_code=409,
                detail="Payment creation is already in progress",
                headers={"Retry-After": str(retry_after)},
            )
        attempt = latest_attempt
        attempt.status = CREATING_PAYMENT_ATTEMPT_STATUS
        attempt.lease_expires_at = lease_expires_at
        attempt.last_error = ""
        attempt.updated_at = now
    else:
        last_attempt_number = (
            db.query(func.max(PaymentCreationAttempt.attempt_number))
            .filter(
                PaymentCreationAttempt.order_id == order.id,
                PaymentCreationAttempt.provider == _PROVIDER,
            )
            .scalar()
            or 0
        )
        attempt = PaymentCreationAttempt(
            order_id=order.id,
            provider=_PROVIDER,
            attempt_number=last_attempt_number + 1,
            status=CREATING_PAYMENT_ATTEMPT_STATUS,
            lease_expires_at=lease_expires_at,
            updated_at=now,
        )
        db.add(attempt)
        db.flush()

    return PaymentCreationClaim(
        order_id=order.id,
        amount=order.total_amount,
        currency=order.currency,
        attempt_id=attempt.id,
    )


def _attempt_error(error: str, fallback: str) -> str:
    normalized = str(error or fallback).strip()[:2000]
    return normalized or fallback


def mark_payment_creation_retry_required(
    db: Session,
    attempt_id: int,
    error: str,
) -> None:
    attempt = (
        db.query(PaymentCreationAttempt)
        .filter(PaymentCreationAttempt.id == attempt_id)
        .with_for_update()
        .first()
    )
    if not attempt or attempt.status in {
        COMPLETED_PAYMENT_ATTEMPT_STATUS,
        ABANDONED_PAYMENT_ATTEMPT_STATUS,
        REVIEW_REQUIRED_PAYMENT_ATTEMPT_STATUS,
    }:
        return
    attempt.status = RETRY_REQUIRED_PAYMENT_ATTEMPT_STATUS
    attempt.lease_expires_at = None
    attempt.last_error = _attempt_error(error, "provider_request_failed")
    attempt.updated_at = datetime.utcnow()


def mark_payment_creation_review_required(
    db: Session,
    attempt_id: int,
    error: str,
) -> None:
    attempt = (
        db.query(PaymentCreationAttempt)
        .filter(PaymentCreationAttempt.id == attempt_id)
        .with_for_update()
        .first()
    )
    if not attempt or attempt.status in {
        COMPLETED_PAYMENT_ATTEMPT_STATUS,
        ABANDONED_PAYMENT_ATTEMPT_STATUS,
    }:
        return
    attempt.status = REVIEW_REQUIRED_PAYMENT_ATTEMPT_STATUS
    attempt.lease_expires_at = None
    attempt.last_error = _attempt_error(error, "provider_review_required")
    attempt.updated_at = datetime.utcnow()


def _queue_payment_review(db: Session, order: Order, payment_id: str, reason: str) -> None:
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


def finalize_payment_creation(
    db: Session,
    attempt_id: int,
    provider_payment_id: str,
    provider_status: str,
    confirmation_url: str,
) -> tuple[Order, Payment]:
    attempt = (
        db.query(PaymentCreationAttempt)
        .filter(PaymentCreationAttempt.id == attempt_id)
        .with_for_update()
        .first()
    )
    if not attempt:
        raise HTTPException(status_code=409, detail="Payment creation attempt is missing")

    if attempt.status == COMPLETED_PAYMENT_ATTEMPT_STATUS and attempt.provider_payment_id:
        existing = (
            db.query(Payment)
            .filter(
                Payment.provider == _PROVIDER,
                Payment.provider_payment_id == attempt.provider_payment_id,
            )
            .first()
        )
        if existing:
            return _payment_for_output(db, existing.id)
        raise HTTPException(status_code=409, detail="Completed payment attempt requires reconciliation")
    if attempt.status in {
        REVIEW_REQUIRED_PAYMENT_ATTEMPT_STATUS,
        ABANDONED_PAYMENT_ATTEMPT_STATUS,
    }:
        raise HTTPException(status_code=409, detail="Payment creation attempt cannot be finalized automatically")

    order = (
        db.query(Order)
        .filter(Order.id == attempt.order_id)
        .with_for_update()
        .first()
    )
    if not order:
        raise HTTPException(status_code=409, detail="Payment order is missing")

    payment = (
        db.query(Payment)
        .filter(
            Payment.provider == _PROVIDER,
            Payment.provider_payment_id == provider_payment_id,
        )
        .with_for_update()
        .first()
    )
    if payment and payment.order_id != order.id:
        raise HTTPException(status_code=409, detail="Provider payment belongs to another order")
    if not payment:
        payment = Payment(
            order_id=order.id,
            provider=_PROVIDER,
            provider_payment_id=provider_payment_id,
            status=provider_status,
            amount=order.total_amount,
            confirmation_url=confirmation_url,
        )
        db.add(payment)
        db.flush()

    attempt.status = COMPLETED_PAYMENT_ATTEMPT_STATUS
    attempt.provider_payment_id = provider_payment_id
    attempt.lease_expires_at = None
    attempt.last_error = ""
    attempt.updated_at = datetime.utcnow()

    if order.payment_status in _SETTLED_ORDER_PAYMENT_STATUSES:
        return order, payment

    if order.status == "cancelled" or order.payment_status == "cancelled":
        if provider_status != "canceled":
            order.status = "payment_review_required"
            order.payment_status = "paid_review_required" if provider_status == "succeeded" else "payment_review_required"
            _queue_payment_review(db, order, provider_payment_id, "payment_created_after_cancel")
        return order, payment

    if (
        order.status not in _ELIGIBLE_ORDER_STATUSES
        or order.payment_status not in _ELIGIBLE_PAYMENT_STATUSES
    ):
        order.status = "payment_review_required"
        order.payment_status = "paid_review_required" if provider_status == "succeeded" else "payment_review_required"
        _queue_payment_review(db, order, provider_payment_id, "payment_created_after_order_transition")
        return order, payment

    order.payment_status = "payment_created"
    order.status = "payment_created"
    return order, payment
