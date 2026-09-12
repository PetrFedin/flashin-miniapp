from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..api import payments as payments_api
from ..models import Order, Payment
from ..services.order_cancellation import cancel_order_before_settlement
from ..services.payment_attempts import resolve_provider_payment_attempt
from ..services.payment_review import ensure_payment_review_case
from ..services.payment_settlement import settle_paid_order

_PROVIDER = "yookassa"
_RECONCILABLE_PAYMENT_STATUSES = frozenset({"pending", "waiting_for_capture", "succeeded"})
_RECONCILABLE_ORDER_PAYMENT_STATUSES = frozenset({"pending", "payment_created", "cancelled"})


def _bounded_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError("Payment reconciliation limit must be an integer")
    if limit < 1 or limit > 200:
        raise ValueError("Payment reconciliation limit must be between 1 and 200")
    return limit


def _candidate_payment_ids(db: Session, *, limit: int) -> list[int]:
    latest_payment_ids = (
        db.query(func.max(Payment.id).label("payment_id"))
        .filter(Payment.provider == _PROVIDER)
        .group_by(Payment.order_id)
        .subquery()
    )
    return [
        row[0]
        for row in (
            db.query(Payment.id)
            .join(latest_payment_ids, Payment.id == latest_payment_ids.c.payment_id)
            .join(Order, Order.id == Payment.order_id)
            .filter(
                Payment.provider == _PROVIDER,
                Payment.provider_payment_id != "",
                Payment.status.in_(_RECONCILABLE_PAYMENT_STATUSES),
                Order.payment_status.in_(_RECONCILABLE_ORDER_PAYMENT_STATUSES),
            )
            .order_by(Payment.created_at.asc(), Payment.id.asc())
            .limit(_bounded_limit(limit))
            .all()
        )
    ]


def _persist_review(
    db: Session,
    *,
    payment_id: int,
    order_id: int,
    provider_payment_id: str,
    reason: str,
    provider_status: str | None = None,
    paid_after_cancel: bool = False,
) -> bool:
    payment = (
        db.query(Payment)
        .filter(Payment.id == payment_id)
        .with_for_update()
        .first()
    )
    order = (
        db.query(Order)
        .filter(Order.id == order_id)
        .with_for_update()
        .first()
    )
    if (
        not payment
        or not order
        or payment.order_id != order.id
        or payment.provider != _PROVIDER
        or payment.provider_payment_id != provider_payment_id
    ):
        db.rollback()
        return False

    if provider_status:
        payment.status = str(provider_status)[:64]
        if provider_status == "succeeded":
            payment.confirmation_url = ""

    order.status = "payment_review_required"
    order.payment_status = "paid_review_required" if paid_after_cancel else "payment_review_required"
    payments_api._queue_payment_review(db, order, provider_payment_id, reason)
    ensure_payment_review_case(
        db,
        {
            "order_id": order.id,
            "provider": _PROVIDER,
            "provider_payment_id": provider_payment_id,
            "reason": reason,
        },
    )
    db.commit()
    return True


async def reconcile_pending_payments(db: Session, limit: int = 50) -> dict[str, int]:
    """Recover unresolved YooKassa payments when an HTTP callback is missed.

    Only the latest provider attempt for each order is eligible. Provider I/O is
    deliberately performed without holding database row locks. The authoritative
    provider object is revalidated after the local rows are locked again, and the
    same settlement/cancellation domain functions used by the webhook path are
    applied. No PaymentEvent row is fabricated because no webhook was received.
    """
    candidate_ids = _candidate_payment_ids(db, limit=limit)
    result = {
        "seen": len(candidate_ids),
        "succeeded": 0,
        "pending": 0,
        "canceled": 0,
        "review_required": 0,
        "provider_errors": 0,
        "skipped": 0,
    }

    for payment_id in candidate_ids:
        snapshot = db.query(Payment).filter(Payment.id == payment_id).first()
        if not snapshot or not snapshot.provider_payment_id:
            db.rollback()
            result["skipped"] += 1
            continue
        order_id = snapshot.order_id
        provider_payment_id = snapshot.provider_payment_id
        db.rollback()

        try:
            provider_payment = await payments_api.fetch_yookassa_payment(provider_payment_id)
        except Exception:
            db.rollback()
            result["provider_errors"] += 1
            continue

        try:
            payment = (
                db.query(Payment)
                .filter(Payment.id == payment_id)
                .with_for_update()
                .first()
            )
            order = (
                db.query(Order)
                .filter(Order.id == order_id)
                .with_for_update()
                .first()
            )
            latest_payment = (
                db.query(Payment)
                .filter(Payment.order_id == order_id, Payment.provider == _PROVIDER)
                .order_by(Payment.id.desc())
                .with_for_update()
                .first()
            )
            if (
                not payment
                or not order
                or not latest_payment
                or latest_payment.id != payment.id
                or payment.provider_payment_id != provider_payment_id
                or payment.status not in _RECONCILABLE_PAYMENT_STATUSES
                or order.payment_status not in _RECONCILABLE_ORDER_PAYMENT_STATUSES
            ):
                db.rollback()
                result["skipped"] += 1
                continue

            if not isinstance(provider_payment, dict):
                raise payments_api.ProviderPaymentIntegrityError(
                    "provider_payment_payload_invalid",
                    "Provider payment payload must be an object",
                )
            try:
                provider_order_id = payments_api._provider_order_id(provider_payment)
            except HTTPException as exc:
                raise payments_api.ProviderPaymentIntegrityError(
                    "provider_payment_order_reference_invalid",
                    "Provider payment has no valid order reference",
                ) from exc
            if provider_order_id != order.id:
                raise payments_api.ProviderPaymentIntegrityError(
                    "provider_payment_order_reference_mismatch",
                    "Provider payment belongs to another order",
                )
            payments_api._validate_provider_amount(provider_payment, order)

            resolution = resolve_provider_payment_attempt(
                provider_payment,
                stored_confirmation_url=payment.confirmation_url,
            )
            payment.status = resolution.status
            payment.confirmation_url = resolution.confirmation_url

            if resolution.outcome == "reuse":
                db.commit()
                result["pending"] += 1
                continue

            if resolution.outcome == "settled":
                if order.status == "cancelled" or order.payment_status == "cancelled":
                    db.rollback()
                    if _persist_review(
                        db,
                        payment_id=payment_id,
                        order_id=order_id,
                        provider_payment_id=provider_payment_id,
                        reason="reconciled_paid_after_cancel",
                        provider_status="succeeded",
                        paid_after_cancel=True,
                    ):
                        result["review_required"] += 1
                    else:
                        result["skipped"] += 1
                    continue

                settle_paid_order(db, order)
                db.commit()
                result["succeeded"] += 1
                continue

            if resolution.outcome == "replace":
                try:
                    cancel_order_before_settlement(db, order, source="provider")
                except HTTPException as exc:
                    db.rollback()
                    if _persist_review(
                        db,
                        payment_id=payment_id,
                        order_id=order_id,
                        provider_payment_id=provider_payment_id,
                        reason=f"reconciled_provider_cancel_conflict:{exc.detail}",
                        provider_status="canceled",
                    ):
                        result["review_required"] += 1
                    else:
                        result["skipped"] += 1
                    continue
                db.commit()
                result["canceled"] += 1
                continue

            db.rollback()
            if _persist_review(
                db,
                payment_id=payment_id,
                order_id=order_id,
                provider_payment_id=provider_payment_id,
                reason=f"reconciled_provider_status_requires_review:{resolution.status}",
                provider_status=resolution.status,
            ):
                result["review_required"] += 1
            else:
                result["skipped"] += 1

        except payments_api.ProviderPaymentIntegrityError as exc:
            db.rollback()
            if _persist_review(
                db,
                payment_id=payment_id,
                order_id=order_id,
                provider_payment_id=provider_payment_id,
                reason=f"reconciliation_integrity:{exc.reason}",
                provider_status=str(provider_payment.get("status") or "").strip().lower()
                if isinstance(provider_payment, dict)
                else None,
            ):
                result["review_required"] += 1
            else:
                result["skipped"] += 1
        except HTTPException as exc:
            db.rollback()
            if _persist_review(
                db,
                payment_id=payment_id,
                order_id=order_id,
                provider_payment_id=provider_payment_id,
                reason=f"reconciliation_domain_conflict:{exc.detail}",
                provider_status=str(provider_payment.get("status") or "").strip().lower()
                if isinstance(provider_payment, dict)
                else None,
            ):
                result["review_required"] += 1
            else:
                result["skipped"] += 1

    return result
