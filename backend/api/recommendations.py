from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..models import Customer, Product, ProductRecommendation
from ..schemas import ProductOut, SizeHelperIn
from ..security import get_current_admin, get_current_customer
from ..services.rbac import require_permission
from ..services.recommendation_engine import personal_recommendations, rebuild_recommendations_v2
from ..services.recommendations import rebuild_basic_recommendations
from ..services.size_helper import suggest_size

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


def _size_recommendation(payload: SizeHelperIn, available_sizes=None) -> dict:
    try:
        return suggest_size(
            payload.height_cm,
            payload.weight_kg,
            payload.usual_size,
            payload.fit_preference,
            available_sizes=available_sizes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/personal/me", response_model=list[ProductOut])
def personal(
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    products = personal_recommendations(db, customer.id)
    product_ids = [product.id for product in products]
    if not product_ids:
        return []
    hydrated = (
        db.query(Product)
        .options(joinedload(Product.images), joinedload(Product.variants))
        .filter(Product.id.in_(product_ids), Product.active.is_(True))
        .all()
    )
    by_id = {product.id: product for product in hydrated}
    return [by_id[product_id] for product_id in product_ids if product_id in by_id]


@router.post("/size-helper")
def size_helper(payload: SizeHelperIn):
    return _size_recommendation(payload)


@router.post("/size-helper/{product_id}")
def product_size_helper(
    product_id: int,
    payload: SizeHelperIn,
    db: Session = Depends(get_db),
):
    product = (
        db.query(Product)
        .options(joinedload(Product.variants))
        .filter(Product.id == product_id, Product.active.is_(True))
        .first()
    )
    if not product:
        raise HTTPException(status_code=404, detail="Product not found or unavailable")

    available_sizes = [
        variant.size
        for variant in product.variants
        if variant.stock_qty >= 0
        and variant.reserved_qty >= 0
        and variant.reserved_qty <= variant.stock_qty
        and variant.available_qty > 0
    ]
    result = _size_recommendation(payload, available_sizes=available_sizes)
    result["product_id"] = product.id
    result["available_sizes"] = available_sizes
    return result


@router.post("/admin/rebuild")
def rebuild(
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "products.write")
    count = rebuild_basic_recommendations(db)
    return {"ok": True, "recommendations": count}


@router.post("/admin/rebuild-v2")
def rebuild_v2(
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "products.write")
    count = rebuild_recommendations_v2(db)
    return {"ok": True, "recommendations": count}


@router.get("/{product_id}", response_model=list[ProductOut])
def product_recommendations(product_id: int, db: Session = Depends(get_db)):
    source_product = (
        db.query(Product.id)
        .filter(Product.id == product_id, Product.active.is_(True))
        .first()
    )
    if not source_product:
        raise HTTPException(status_code=404, detail="Product not found or unavailable")

    rows = (
        db.query(ProductRecommendation)
        .filter(ProductRecommendation.product_id == product_id)
        .order_by(ProductRecommendation.score.desc(), ProductRecommendation.id.asc())
        .limit(8)
        .all()
    )
    recommendation_ids = [row.recommended_product_id for row in rows]
    if not recommendation_ids:
        return []

    products = (
        db.query(Product)
        .options(joinedload(Product.images), joinedload(Product.variants))
        .filter(Product.id.in_(recommendation_ids), Product.active.is_(True))
        .all()
    )
    by_id = {product.id: product for product in products}
    return [by_id[product_id] for product_id in recommendation_ids if product_id in by_id]
