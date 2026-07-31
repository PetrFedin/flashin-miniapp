from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import MoySkladSyncLog
from ..schemas import MoySkladSyncOut
from ..security import get_current_admin
from ..services.moysklad import MoySkladSyncInProgress, sync_assortment_to_catalog
from ..services.rbac import require_permission

router = APIRouter(prefix="/moysklad", tags=["moysklad"])


@router.post("/sync", response_model=MoySkladSyncOut)
async def sync_moysklad(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.write")
    try:
        result = await sync_assortment_to_catalog(db, sync_type="manual")
    except MoySkladSyncInProgress as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result.status == "failed":
        raise HTTPException(
            status_code=502,
            detail={
                "message": "MoySklad synchronization failed",
                "sync_log_id": result.id,
                "error": result.error,
            },
        )
    return result


@router.get("/sync-logs", response_model=list[MoySkladSyncOut])
def sync_logs(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.read")
    return (
        db.query(MoySkladSyncLog)
        .order_by(MoySkladSyncLog.created_at.desc(), MoySkladSyncLog.id.desc())
        .limit(50)
        .all()
    )
