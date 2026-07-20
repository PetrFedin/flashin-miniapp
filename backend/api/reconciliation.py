from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import StockReconciliationLog
from ..schemas import StockReconciliationOut
from ..security import get_current_admin
from ..services.rbac import require_permission

router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])


@router.get("/stock", response_model=list[StockReconciliationOut])
def stock_reconciliation_logs(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "inventory.read")
    return db.query(StockReconciliationLog).order_by(StockReconciliationLog.created_at.desc()).limit(200).all()
