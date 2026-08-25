from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Payment, PaymentReconciliation
from ..schemas import PaymentReconciliationOut
from ..security import get_current_admin
from ..services.audit import log_admin_action
from ..services.payment_reconciliation import create_reconciliation_row, resolve_reconciliation
from ..services.payments import fetch_yookassa_payment
from ..services.rbac import (
    PAYMENT_RECONCILIATION_READ_PERMISSION,
    PAYMENT_RECONCILIATION_WRITE_PERMISSION,
    require_permission,
)

router = APIRouter(prefix="/payment-reconciliation", tags=["payment-reconciliation"])


def _provider_payment_snapshot(provider: object) -> tuple[str, object]:
    if not isinstance(provider, dict):
        raise HTTPException(status_code=502, detail="Payment provider returned an invalid payload")
    status = str(provider.get("status") or "").strip()
    amount_payload = provider.get("amount")
    amount = amount_payload.get("value") if isinstance(amount_payload, dict) else None
    if not status or amount in (None, ""):
        raise HTTPException(status_code=502, detail="Payment provider returned incomplete reconciliation data")
    return status, amount


@router.get("", response_model=list[PaymentReconciliationOut])
def list_reconciliation(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, PAYMENT_RECONCILIATION_READ_PERMISSION)
    return (
        db.query(PaymentReconciliation)
        .order_by(PaymentReconciliation.created_at.desc())
        .limit(300)
        .all()
    )


@router.post("/payments/{payment_id}/check", response_model=PaymentReconciliationOut)
async def check_payment(
    payment_id: int,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, PAYMENT_RECONCILIATION_WRITE_PERMISSION)
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    if not payment.provider_payment_id:
        raise HTTPException(status_code=409, detail="Payment has no provider payment id")

    provider = await fetch_yookassa_payment(payment.provider_payment_id)
    provider_status, provider_amount = _provider_payment_snapshot(provider)
    try:
        row = create_reconciliation_row(db, payment, provider_status, provider_amount)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail="Payment provider returned invalid reconciliation data") from exc

    db.flush()
    log_admin_action(
        db,
        admin,
        "payment.reconciliation.check",
        "payment_reconciliation",
        row.id,
        {
            "payment_id": payment.id,
            "order_id": payment.order_id,
            "status": row.status,
        },
    )
    db.commit()
    db.refresh(row)
    return row


@router.post("/{row_id}/resolve")
def resolve(
    row_id: int,
    message: str = "",
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, PAYMENT_RECONCILIATION_WRITE_PERMISSION)
    row = (
        db.query(PaymentReconciliation)
        .filter(PaymentReconciliation.id == row_id)
        .with_for_update()
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Reconciliation row not found")
    if row.status == "resolved":
        return {"ok": True, "idempotent": True}

    previous_status = row.status
    resolve_reconciliation(row, message)
    log_admin_action(
        db,
        admin,
        "payment.reconciliation.resolve",
        "payment_reconciliation",
        row.id,
        {
            "payment_id": row.payment_id,
            "order_id": row.order_id,
            "from_status": previous_status,
            "status": row.status,
        },
    )
    db.commit()
    return {"ok": True, "idempotent": False}
