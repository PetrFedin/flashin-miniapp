from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..database import get_db
from ..schemas import MoySkladSyncOut
from ..security import get_current_admin
from ..services.moysklad import sync_assortment_to_catalog
from ..services.rbac import require_permission
from ..models import MoySkladSyncLog

router = APIRouter(prefix="/moysklad", tags=["moysklad"])


@router.post("/sync", response_model=MoySkladSyncOut)
async def sync_moysklad(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.write")
    return await sync_assortment_to_catalog(db, sync_type="manual")


@router.get("/sync-logs", response_model=list[MoySkladSyncOut])
def sync_logs(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.read")
    return db.query(MoySkladSyncLog).order_by(MoySkladSyncLog.created_at.desc()).limit(50).all()
