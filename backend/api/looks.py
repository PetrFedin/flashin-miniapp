from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..models import Look, LookItem, Product
from ..schemas import LookCreate, ProductOut
from ..security import get_current_admin
from ..services.rbac import require_permission

router = APIRouter(prefix="/looks", tags=["looks"])


class LookDetailOut(BaseModel):
    id: int
    title: str
    description: str
    products: list[ProductOut] = Field(default_factory=list)


def _serialize_look(look: Look, ordered_products: list[Product]) -> LookDetailOut:
    return LookDetailOut(
        id=look.id,
        title=look.title,
        description=look.description,
        products=[ProductOut.model_validate(product) for product in ordered_products],
    )


def _load_look_products(db: Session, look_id: int) -> list[Product]:
    return (
        db.query(Product)
        .join(LookItem, LookItem.product_id == Product.id)
        .options(joinedload(Product.images), joinedload(Product.variants))
        .filter(LookItem.look_id == look_id, Product.active.is_(True))
        .order_by(LookItem.sort_order.asc(), LookItem.id.asc())
        .all()
    )


@router.get("", response_model=list[LookDetailOut])
def list_looks(db: Session = Depends(get_db)):
    looks = (
        db.query(Look)
        .filter(Look.active.is_(True))
        .order_by(Look.created_at.desc(), Look.id.desc())
        .all()
    )
    result: list[LookDetailOut] = []
    for look in looks:
        products = _load_look_products(db, look.id)
        if products:
            result.append(_serialize_look(look, products))
    return result


@router.post("", response_model=LookDetailOut)
def create_look(
    payload: LookCreate,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "products.write")
    title = (payload.title or "").strip()
    description = (payload.description or "").strip()
    product_ids = list(dict.fromkeys(payload.product_ids))

    if not title:
        raise HTTPException(status_code=400, detail="Look title is required")
    if len(title) > 255 or len(description) > 2000:
        raise HTTPException(status_code=400, detail="Look content is too long")
    if not product_ids:
        raise HTTPException(status_code=400, detail="Look must contain at least one product")
    if len(product_ids) != len(payload.product_ids):
        raise HTTPException(status_code=409, detail="Look contains duplicate products")
    if len(product_ids) > 24:
        raise HTTPException(status_code=400, detail="Look contains too many products")

    products = (
        db.query(Product)
        .options(joinedload(Product.images), joinedload(Product.variants))
        .filter(Product.id.in_(product_ids), Product.active.is_(True))
        .all()
    )
    by_id = {product.id: product for product in products}
    missing_ids = [product_id for product_id in product_ids if product_id not in by_id]
    if missing_ids:
        raise HTTPException(
            status_code=409,
            detail={"message": "Look contains unavailable products", "product_ids": missing_ids},
        )

    try:
        look = Look(title=title, description=description, active=True)
        db.add(look)
        db.flush()
        for index, product_id in enumerate(product_ids):
            db.add(LookItem(look_id=look.id, product_id=product_id, sort_order=index))
        db.commit()
    except Exception:
        db.rollback()
        raise

    return _serialize_look(look, [by_id[product_id] for product_id in product_ids])
