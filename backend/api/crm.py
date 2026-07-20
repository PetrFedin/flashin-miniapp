from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import CrmProfile
from ..schemas import CrmProfileOut
from ..security import get_current_admin
from ..services.crm import recompute_all_profiles
from ..services.rbac import require_permission

router = APIRouter(prefix="/crm", tags=["crm"])


@router.post("/recompute")
def recompute(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "crm.write")
    count = recompute_all_profiles(db)
    return {"ok": True, "profiles": count}


@router.get("/profiles", response_model=list[CrmProfileOut])
def profiles(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "crm.read")
    return db.query(CrmProfile).order_by(CrmProfile.total_spent.desc()).limit(500).all()
