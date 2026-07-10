from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import Look, LookItem
from ..schemas import LookCreate, LookOut
from ..security import get_current_admin
from ..services.rbac import require_permission

router = APIRouter(prefix="/looks", tags=["looks"])


def serialize_look(look: Look, db: Session) -> LookOut:
    items = db.query(LookItem).filter(LookItem.look_id == look.id).order_by(LookItem.sort_order).all()
    return LookOut(id=look.id, title=look.title, description=look.description, product_ids=[i.product_id for i in items])


@router.get("", response_model=list[LookOut])
def list_looks(db: Session = Depends(get_db)):
    looks = db.query(Look).filter(Look.active == True).order_by(Look.created_at.desc()).all()
    return [serialize_look(l, db) for l in looks]


@router.post("", response_model=LookOut)
def create_look(payload: LookCreate, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.write")
    look = Look(title=payload.title, description=payload.description, active=True)
    db.add(look)
    db.flush()
    for idx, product_id in enumerate(payload.product_ids):
        db.add(LookItem(look_id=look.id, product_id=product_id, sort_order=idx))
    db.commit()
    return serialize_look(look, db)
