from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..database import utcnow_naive
from ..models import Order, Payment, PaymentReconciliation
from .pilot_circuit_breaker import stop_pilot_for_order

_MONEY_STEP = Decimal("0.01")


def _money(value, field: str) -> Decimal:
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field}") from exc
    if not amount.is_finite():
        raise ValueError(f"Invalid {field}")
    return amount.quantize(_MONEY_STEP, rounding=ROUND_HALF_UP)


def lock_fresh_payment_for_reconciliation(
    db: Session,
    payment_id: int,
    *,
    expected_order_id: int,
    expected_provider_payment_id: str,
) -> Payment:
    # Order is the transaction root for payment mutations elsewhere in the
    # system. Keep reconciliation on the same Order -> Payment lock order.
    order = (
        db.query(Order)
        .filter(Order.id == expected_order_id)
        .with_for_update()
        .first()
    )
    if not order:
        raise HTTPException(status_code=409, detail="Payment order changed during reconciliation")

    payment = (
        db.query(Payment)
        .filter(Payment.id == payment_id)
        .with_for_update()
        .first()
    )
    if not payment:
        raise HTTPException(status_code=409, detail="Payment changed during reconciliation")
    if int(payment.order_id) != int(expected_order_id):
        raise HTTPException(status_code=409, detail="Payment order changed during reconciliation")
    if str(payment.provider_payment_id or "") != str(expected_provider_payment_id or ""):
        raise HTTPException(
            status_code=409,
            detail="Payment provider identifier changed during reconciliation",
        )
    return payment


def create_reconciliation_row(
    db: Session,
    payment: Payment,
    provider_status: str,
    provider_amount,
) -> PaymentReconciliation:
    local_amount = _money(payment.amount, "local payment amount")
    normalized_provider_amount = _money(provider_amount, "provider payment amount")
    status = (
        "matched"
        if payment.status == provider_status and local_amount == normalized_provider_amount
        else "mismatch"
    )
    if status == "mismatch":
        stop_pilot_for_order(
            db,
            order_id=payment.order_id,
            reason="payment_reconciliation_mismatch",
        )
    row = PaymentReconciliation(
        payment_id=payment.id,
        order_id=payment.order_id,
        provider_payment_id=payment.provider_payment_id,
        local_status=payment.status,
        provider_status=provider_status,
        amount_local=local_amount,
        amount_provider=normalized_provider_amount,
        status=status,
        message="" if status == "matched" else "Local/provider payment data mismatch",
    )
    db.add(row)
    return row


def resolve_reconciliation(row: PaymentReconciliation, message: str = "") -> None:
    row.status = "resolved"
    row.message = message or row.message
    row.resolved_at = utcnow_naive()
