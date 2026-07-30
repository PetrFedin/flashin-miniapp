from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import CheckConstraint, event

from .models import Payment
from .payment_statuses import PROVIDER_PAYMENT_STATUSES, PROVIDER_PAYMENT_STATUS_SQL


def _constraint_names() -> set[str]:
    return {constraint.name for constraint in Payment.__table__.constraints if constraint.name}


def _check(name: str, expression: str) -> None:
    if name not in _constraint_names():
        Payment.__table__.append_constraint(CheckConstraint(expression, name=name))


def _normalize_before_write(_mapper, _connection, target: Payment) -> None:
    provider = str(target.provider or "").strip().lower()
    provider_payment_id = str(target.provider_payment_id or "").strip()
    status = str(target.status or "").strip().lower()
    if status == "paid":
        status = "succeeded"
    confirmation_url = str(target.confirmation_url or "").strip()

    if not provider or len(provider) > 64:
        raise HTTPException(status_code=400, detail="Payment provider is invalid")
    if not provider_payment_id or len(provider_payment_id) > 255:
        raise HTTPException(status_code=400, detail="Provider payment id is invalid")
    if status not in PROVIDER_PAYMENT_STATUSES:
        raise HTTPException(status_code=400, detail="Provider payment status is invalid")
    if len(confirmation_url) > 2048:
        raise HTTPException(status_code=400, detail="Payment confirmation URL is too long")

    target.provider = provider
    target.provider_payment_id = provider_payment_id
    target.status = status
    target.confirmation_url = confirmation_url


def apply_payment_record_constraints() -> None:
    _check(
        "ck_payments_provider_normalized",
        "length(trim(provider)) > 0 AND provider = lower(trim(provider))",
    )
    _check(
        "ck_payments_provider_payment_id_normalized",
        "length(trim(provider_payment_id)) > 0 AND provider_payment_id = trim(provider_payment_id)",
    )
    _check(
        "ck_payments_status_valid",
        f"status IN ({PROVIDER_PAYMENT_STATUS_SQL})",
    )
    _check(
        "ck_payments_confirmation_url_normalized",
        "confirmation_url = trim(confirmation_url)",
    )

    for event_name in ("before_insert", "before_update"):
        if not event.contains(Payment, event_name, _normalize_before_write):
            event.listen(Payment, event_name, _normalize_before_write)


apply_payment_record_constraints()
