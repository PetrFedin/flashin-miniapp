from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import ProductRecommendation
from ..schemas import RecommendationOut, SizeHelperIn
from ..security import get_current_admin
from ..services.recommendations import rebuild_basic_recommendations
from ..services.size_helper import suggest_size
from ..services.rbac import require_permission

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.get("/{product_id}", response_model=list[RecommendationOut])
def product_recommendations(product_id: int, db: Session = Depends(get_db)):
    return db.query(ProductRecommendation).filter(ProductRecommendation.product_id == product_id).order_by(ProductRecommendation.score.desc()).limit(8).all()


@router.post("/admin/rebuild")
def rebuild(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.write")
    count = rebuild_basic_recommendations(db)
    return {"ok": True, "recommendations": count}


@router.post("/size-helper")
def size_helper(payload: SizeHelperIn):
    return suggest_size(payload.height_cm, payload.weight_kg, payload.usual_size, payload.fit_preference)



@router.get("/personal/me")
def personal(customer=Depends(__import__("backend.security", fromlist=["get_current_customer"]).get_current_customer), db: Session = Depends(get_db)):
    from ..services.recommendation_engine import personal_recommendations
    return personal_recommendations(db, customer.id)


@router.post("/admin/rebuild-v2")
def rebuild_v2(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "products.write")
    from ..services.recommendation_engine import rebuild_recommendations_v2
    count = rebuild_recommendations_v2(db)
    return {"ok": True, "recommendations": count}
