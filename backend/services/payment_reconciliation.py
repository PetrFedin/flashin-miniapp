from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy.orm import Session

from ..database import utcnow_naive
from ..models import Payment, PaymentReconciliation
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
