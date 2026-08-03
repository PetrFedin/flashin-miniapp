from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy.orm import Session

from ..models import Order, Payment, PaymentReconciliation


_MONEY_STEP = Decimal("0.01")
_REVIEW_EVENT = "payment.review_required"


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive integer") from exc
    if parsed < 1:
        raise ValueError(f"{field} must be a positive integer")
    return parsed


def _money(value: object, field: str) -> float:
    try:
        amount = Decimal(str(value)).quantize(_MONEY_STEP, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} is invalid") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"{field} is invalid")
    return float(amount)


def _review_message(reason: object) -> str:
    normalized = " ".join(str(reason or "").strip().split())
    if not normalized:
        raise ValueError("Payment review reason is required")
    return f"{_REVIEW_EVENT}:{normalized[:480]}"


def ensure_payment_review_case(
    db: Session,
    payload: dict,
) -> PaymentReconciliation:
    """Create one open reconciliation case for a payment-review reason.

    The payment webhook event remains the concurrency boundary. This helper is
    additionally idempotent for direct/replayed domain-event production and
    refreshes the evidence snapshot when the same case already exists.
    """
    if not isinstance(payload, dict):
        raise ValueError("Payment review payload must be an object")

    order_id = _positive_int(payload.get("order_id"), "order_id")
    provider_payment_id = str(payload.get("provider_payment_id") or "").strip()
    if not provider_payment_id or len(provider_payment_id) > 255:
        raise ValueError("provider_payment_id is invalid")
    message = _review_message(payload.get("reason"))

    payment = (
        db.query(Payment)
        .filter(
            Payment.order_id == order_id,
            Payment.provider_payment_id == provider_payment_id,
        )
        .with_for_update()
        .first()
    )
    if not payment:
        raise ValueError("Payment review references an unknown payment")

    order = (
        db.query(Order)
        .filter(Order.id == order_id)
        .with_for_update()
        .first()
    )
    if not order:
        raise ValueError("Payment review references an unknown order")

    local_amount = _money(payment.amount, "local payment amount")
    provider_amount = _money(order.total_amount, "provider payment amount")

    existing = (
        db.query(PaymentReconciliation)
        .filter(
            PaymentReconciliation.payment_id == payment.id,
            PaymentReconciliation.status == "open",
            PaymentReconciliation.message == message,
        )
        .with_for_update()
        .first()
    )
    if existing:
        existing.order_id = order.id
        existing.provider_payment_id = provider_payment_id
        existing.local_status = str(order.payment_status or "")[:64]
        existing.provider_status = str(payment.status or "")[:64]
        existing.amount_local = local_amount
        existing.amount_provider = provider_amount
        return existing

    row = PaymentReconciliation(
        payment_id=payment.id,
        order_id=order.id,
        provider_payment_id=provider_payment_id,
        local_status=str(order.payment_status or "")[:64],
        provider_status=str(payment.status or "")[:64],
        amount_local=local_amount,
        amount_provider=provider_amount,
        status="open",
        message=message,
    )
    db.add(row)
    return row
