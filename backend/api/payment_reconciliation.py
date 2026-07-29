from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Payment, PaymentReconciliation
from ..schemas import PaymentReconciliationOut
from ..security import get_current_admin
from ..services.payment_reconciliation import (
    create_reconciliation_row,
    parse_provider_payment_contract,
    resolve_reconciliation,
)
from ..services.payments import fetch_yookassa_payment
from ..services.rbac import require_permission

router = APIRouter(prefix="/payment-reconciliation", tags=["payment-reconciliation"])


@router.get("", response_model=list[PaymentReconciliationOut])
def list_reconciliation(
    limit: int = Query(default=300, ge=1, le=500),
    status: str | None = Query(default=None, max_length=64),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "orders.read")
    query = db.query(PaymentReconciliation)
    normalized_status = (status or "").strip().lower()
    if normalized_status:
        if normalized_status not in {"matched", "mismatch", "resolved"}:
            raise HTTPException(status_code=400, detail="Invalid reconciliation status")
        query = query.filter(PaymentReconciliation.status == normalized_status)
    return (
        query.order_by(PaymentReconciliation.created_at.desc(), PaymentReconciliation.id.desc())
        .limit(limit)
        .all()
    )


@router.post("/payments/{payment_id}/check", response_model=PaymentReconciliationOut)
async def check_payment(
    payment_id: int,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "orders.write")

    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    provider_payment_id = str(payment.provider_payment_id or "").strip()
    if not provider_payment_id:
        raise HTTPException(status_code=409, detail="Payment has no provider identifier")

    # Release the read transaction before the external HTTP request. The payment
    # is reloaded and locked by create_reconciliation_row before any write.
    db.rollback()
    provider = await fetch_yookassa_payment(provider_payment_id)
    provider_status, provider_amount, provider_currency = parse_provider_payment_contract(
        provider,
        provider_payment_id,
    )

    try:
        row = create_reconciliation_row(
            db,
            payment_id,
            provider_payment_id,
            provider_status,
            provider_amount,
            provider_currency,
        )
        db.commit()
        db.refresh(row)
        return row
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


@router.post("/{row_id}/resolve")
def resolve(
    row_id: int,
    message: str = Query(default="", max_length=2000),
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "orders.write")
    try:
        row = (
            db.query(PaymentReconciliation)
            .filter(PaymentReconciliation.id == row_id)
            .with_for_update()
            .first()
        )
        if not row:
            raise HTTPException(status_code=404, detail="Reconciliation row not found")
        changed = resolve_reconciliation(row, message)
        db.commit()
        return {"ok": True, "idempotent": not changed}
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
