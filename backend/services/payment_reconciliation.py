from datetime import datetime
from sqlalchemy.orm import Session
from ..models import Order, Payment, PaymentReconciliation


def create_reconciliation_row(db: Session, payment: Payment, provider_status: str, provider_amount: float) -> PaymentReconciliation:
    order = db.query(Order).filter(Order.id == payment.order_id).first()
    local_amount = payment.amount
    status = "matched" if payment.status == provider_status and abs(local_amount - provider_amount) < 0.01 else "mismatch"
    row = PaymentReconciliation(
        payment_id=payment.id,
        order_id=payment.order_id,
        provider_payment_id=payment.provider_payment_id,
        local_status=payment.status,
        provider_status=provider_status,
        amount_local=local_amount,
        amount_provider=provider_amount,
        status=status,
        message="" if status == "matched" else "Local/provider payment data mismatch",
    )
    db.add(row)
    return row


def resolve_reconciliation(row: PaymentReconciliation, message: str = "") -> None:
    row.status = "resolved"
    row.message = message or row.message
    row.resolved_at = datetime.utcnow()
