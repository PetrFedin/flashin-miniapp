from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..database import get_db
from ..security import get_current_admin
from ..services.diagnostics import run_diagnostics
from ..services.rbac import require_permission

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])


@router.get("")
def diagnostics(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "orders.read")
    return run_diagnostics(db)
