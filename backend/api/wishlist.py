from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from ..database import get_db
from ..models import Customer, Product, WishlistItem
from ..schemas import ProductOut, WishlistIn
from ..security import get_current_customer

router = APIRouter(prefix="/wishlist", tags=["wishlist"])


def _load_product(db: Session, product_id: int) -> Product | None:
    return (
        db.query(Product)
        .options(joinedload(Product.images), joinedload(Product.variants))
        .filter(Product.id == product_id, Product.active.is_(True))
        .first()
    )


@router.get("", response_model=list[ProductOut])
def list_wishlist(
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    items = (
        db.query(WishlistItem)
        .options(
            joinedload(WishlistItem.product).joinedload(Product.images),
            joinedload(WishlistItem.product).joinedload(Product.variants),
        )
        .join(Product, Product.id == WishlistItem.product_id)
        .filter(WishlistItem.customer_id == customer.id, Product.active.is_(True))
        .order_by(WishlistItem.created_at.desc(), WishlistItem.id.desc())
        .all()
    )
    return [item.product for item in items]


@router.post("", response_model=ProductOut)
def add_wishlist(
    payload: WishlistIn,
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    product = _load_product(db, payload.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found or unavailable")

    try:
        existing = (
            db.query(WishlistItem)
            .filter(
                WishlistItem.customer_id == customer.id,
                WishlistItem.product_id == product.id,
            )
            .with_for_update()
            .first()
        )
        if not existing:
            db.add(WishlistItem(customer_id=customer.id, product_id=product.id))
            db.commit()
        else:
            db.rollback()
        return product
    except IntegrityError:
        db.rollback()
        product = _load_product(db, payload.product_id)
        if product:
            return product
        raise HTTPException(status_code=409, detail="Wishlist item could not be saved")
    except Exception:
        db.rollback()
        raise


@router.delete("/{product_id}")
def remove_wishlist(
    product_id: int,
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    try:
        item = (
            db.query(WishlistItem)
            .filter(
                WishlistItem.customer_id == customer.id,
                WishlistItem.product_id == product_id,
            )
            .with_for_update()
            .first()
        )
        removed = bool(item)
        if item:
            db.delete(item)
        db.commit()
        return {"ok": True, "product_id": product_id, "removed": removed}
    except Exception:
        db.rollback()
        raise
