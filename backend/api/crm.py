from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import CrmProfile
from ..schemas import CrmProfileOut
from ..security import get_current_admin
from ..services.audit import log_admin_action
from ..services.crm import recompute_all_profiles
from ..services.rbac import CRM_RECOMPUTE_PERMISSION, require_permission

router = APIRouter(prefix="/crm", tags=["crm"])


@router.post("/recompute")
def recompute(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, CRM_RECOMPUTE_PERMISSION)
    try:
        count = recompute_all_profiles(db)
        log_admin_action(
            db,
            admin,
            "crm.profiles.recompute",
            entity_type="crm_profile",
            payload={"profiles": count},
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"ok": True, "profiles": count}


@router.get("/profiles", response_model=list[CrmProfileOut])
def profiles(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "customers.read")
    return db.query(CrmProfile).order_by(CrmProfile.total_spent.desc()).limit(500).all()
