import hashlib
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..models import Order, Payment, PaymentReconciliation

_MONEY_STEP = Decimal("0.01")
_PROVIDER_PAYMENT_STATUSES = frozenset({"pending", "waiting_for_capture", "succeeded", "canceled"})
_MAX_RESOLUTION_MESSAGE_LENGTH = 2000


def reconciliation_money(value: object, field: str) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(_MONEY_STEP, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(status_code=409, detail=f"Invalid {field}")
    if not amount.is_finite() or amount < 0:
        raise HTTPException(status_code=409, detail=f"Invalid {field}")
    return amount


def parse_provider_payment_contract(
    provider_payment: object,
    expected_payment_id: str,
) -> tuple[str, Decimal, str]:
    if not isinstance(provider_payment, dict):
        raise HTTPException(status_code=502, detail="Payment provider returned an invalid payload")

    provider_payment_id = provider_payment.get("id")
    if not isinstance(provider_payment_id, str) or provider_payment_id.strip() != expected_payment_id:
        raise HTTPException(status_code=409, detail="Payment provider returned another payment")

    raw_status = provider_payment.get("status")
    if not isinstance(raw_status, str):
        raise HTTPException(status_code=502, detail="Payment provider returned an invalid status")
    provider_status = raw_status.strip().lower()
    if provider_status not in _PROVIDER_PAYMENT_STATUSES:
        raise HTTPException(status_code=502, detail="Payment provider returned an invalid status")

    amount = provider_payment.get("amount")
    if not isinstance(amount, dict):
        raise HTTPException(status_code=502, detail="Payment provider returned an invalid amount")
    try:
        provider_amount = Decimal(str(amount.get("value"))).quantize(
            _MONEY_STEP,
            rounding=ROUND_HALF_UP,
        )
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(status_code=502, detail="Payment provider returned an invalid amount")
    if not provider_amount.is_finite() or provider_amount <= 0:
        raise HTTPException(status_code=502, detail="Payment provider returned an invalid amount")

    provider_currency = str(amount.get("currency") or "").strip().upper()
    if len(provider_currency) != 3 or not provider_currency.isalpha():
        raise HTTPException(status_code=502, detail="Payment provider returned an invalid currency")

    return provider_status, provider_amount, provider_currency


def _advisory_lock_key(payment_id: int) -> int:
    digest = hashlib.sha256(f"payment-reconciliation:{payment_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def acquire_payment_reconciliation_lock(db: Session, payment_id: int) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": _advisory_lock_key(payment_id)},
    )


def _reconciliation_result(
    local_status: str,
    provider_status: str,
    local_amount: Decimal,
    provider_amount: Decimal,
    local_currency: str,
    provider_currency: str,
) -> tuple[str, str]:
    mismatches: list[str] = []
    if local_status != provider_status:
        mismatches.append(f"status {local_status!r} != {provider_status!r}")
    if local_amount != provider_amount:
        mismatches.append(f"amount {local_amount:.2f} != {provider_amount:.2f}")
    if local_currency != provider_currency:
        mismatches.append(f"currency {local_currency!r} != {provider_currency!r}")
    if not mismatches:
        return "matched", ""
    return "mismatch", "Local/provider mismatch: " + "; ".join(mismatches)


def create_reconciliation_row(
    db: Session,
    payment_id: int,
    expected_provider_payment_id: str,
    provider_status: str,
    provider_amount: Decimal,
    provider_currency: str,
) -> PaymentReconciliation:
    acquire_payment_reconciliation_lock(db, payment_id)

    payment = (
        db.query(Payment)
        .filter(Payment.id == payment_id)
        .with_for_update()
        .first()
    )
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment.provider_payment_id != expected_provider_payment_id:
        raise HTTPException(status_code=409, detail="Payment changed during reconciliation")

    order = (
        db.query(Order)
        .filter(Order.id == payment.order_id)
        .with_for_update()
        .first()
    )
    if not order:
        raise HTTPException(status_code=409, detail="Payment order is missing")

    local_status = str(payment.status or "").strip().lower()
    if local_status not in _PROVIDER_PAYMENT_STATUSES:
        raise HTTPException(status_code=409, detail="Local payment status is invalid")
    local_amount = reconciliation_money(payment.amount, "local payment amount")
    local_currency = str(order.currency or "").strip().upper()
    if len(local_currency) != 3 or not local_currency.isalpha():
        raise HTTPException(status_code=409, detail="Local payment currency is invalid")

    normalized_provider_status = str(provider_status or "").strip().lower()
    if normalized_provider_status not in _PROVIDER_PAYMENT_STATUSES:
        raise HTTPException(status_code=502, detail="Payment provider returned an invalid status")
    normalized_provider_amount = reconciliation_money(provider_amount, "provider payment amount")
    normalized_provider_currency = str(provider_currency or "").strip().upper()
    if len(normalized_provider_currency) != 3 or not normalized_provider_currency.isalpha():
        raise HTTPException(status_code=502, detail="Payment provider returned an invalid currency")

    status, message = _reconciliation_result(
        local_status,
        normalized_provider_status,
        local_amount,
        normalized_provider_amount,
        local_currency,
        normalized_provider_currency,
    )

    latest = (
        db.query(PaymentReconciliation)
        .filter(PaymentReconciliation.payment_id == payment.id)
        .order_by(PaymentReconciliation.created_at.desc(), PaymentReconciliation.id.desc())
        .with_for_update()
        .first()
    )
    if (
        latest
        and latest.provider_payment_id == payment.provider_payment_id
        and latest.local_status == local_status
        and latest.provider_status == normalized_provider_status
        and reconciliation_money(latest.amount_local, "stored local amount") == local_amount
        and reconciliation_money(latest.amount_provider, "stored provider amount") == normalized_provider_amount
        and latest.status == status
        and latest.message == message
    ):
        return latest

    row = PaymentReconciliation(
        payment_id=payment.id,
        order_id=payment.order_id,
        provider_payment_id=payment.provider_payment_id,
        local_status=local_status,
        provider_status=normalized_provider_status,
        amount_local=float(local_amount),
        amount_provider=float(normalized_provider_amount),
        status=status,
        message=message,
    )
    db.add(row)
    db.flush()
    return row


def resolve_reconciliation(row: PaymentReconciliation, message: str = "") -> bool:
    normalized_message = (message or "").strip()
    if len(normalized_message) > _MAX_RESOLUTION_MESSAGE_LENGTH:
        raise HTTPException(status_code=400, detail="Resolution message is too long")

    if row.status == "resolved" or row.resolved_at is not None:
        if normalized_message and normalized_message != row.message:
            raise HTTPException(status_code=409, detail="Reconciliation row is already resolved")
        return False

    row.status = "resolved"
    if normalized_message:
        row.message = normalized_message
    row.resolved_at = datetime.utcnow()
    return True
