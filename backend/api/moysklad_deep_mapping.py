from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import MoySkladSkuMatch
from ..schemas import MoySkladSkuMatchOut
from ..security import get_current_admin
from ..services.rbac import require_permission
from ..services.moysklad_deep_mapping import confirm_sku_match

router = APIRouter(prefix="/moysklad-deep-mapping", tags=["moysklad-deep-mapping"])


@router.get("/sku-matches", response_model=list[MoySkladSkuMatchOut])
def sku_matches(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.read")
    return db.query(MoySkladSkuMatch).order_by(MoySkladSkuMatch.id.desc()).limit(300).all()


@router.post("/sku-matches/{match_id}/confirm")
def confirm(match_id: int, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.write")
    row = db.query(MoySkladSkuMatch).filter(MoySkladSkuMatch.id == match_id).first()
    if not row:
        raise HTTPException(status_code=404, detail="Match not found")
    confirm_sku_match(row)
    db.commit()
    return {"ok": True}
