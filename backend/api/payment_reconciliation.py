from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import Payment, PaymentReconciliation
from ..schemas import PaymentReconciliationOut
from ..security import get_current_admin
from ..services.payment_reconciliation import create_reconciliation_row, resolve_reconciliation
from ..services.payments import fetch_yookassa_payment
from ..services.rbac import require_permission

router = APIRouter(prefix="/payment-reconciliation", tags=["payment-reconciliation"])


@router.get("", response_model=list[PaymentReconciliationOut])
def list_reconciliation(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "orders.read")
    return db.query(PaymentReconciliation).order_by(PaymentReconciliation.created_at.desc()).limit(300).all()


@router.post("/payments/{payment_id}/check", response_model=PaymentReconciliationOut)
async def check_payment(payment_id: int, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "orders.write")
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    provider = await fetch_yookassa_payment(payment.provider_payment_id)
    status = provider.get("status", "")
    amount = float(provider.get("amount", {}).get("value") or 0)
    row = create_reconciliation_row(db, payment, status, amount)
    db.commit()
    db.refresh(row)
    return row


@router.post("/{row_id}/resolve")
def resolve(row_id: int, message: str = "", admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "orders.write")
    row = db.query(PaymentReconciliation).filter(PaymentReconciliation.id == row_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Reconciliation row not found")
    resolve_reconciliation(row, message)
    db.commit()
    return {"ok": True}
