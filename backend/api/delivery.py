from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import DeliveryZone
from ..schemas import DeliveryZoneCreate
from ..security import get_current_admin

router = APIRouter(prefix="/delivery", tags=["delivery"])


@router.get("/zones")
def list_delivery_zones(db: Session = Depends(get_db)):
    return db.query(DeliveryZone).filter(DeliveryZone.active == True).all()


@router.post("/zones")
def create_delivery_zone(payload: DeliveryZoneCreate, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    zone = DeliveryZone(**payload.model_dump())
    db.add(zone)
    db.commit()
    db.refresh(zone)
    return zone
