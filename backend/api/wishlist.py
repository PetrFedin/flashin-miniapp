from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..models import Customer, Product, WishlistItem
from ..schemas import ProductOut, WishlistIn
from ..security import get_current_customer

router = APIRouter(prefix="/wishlist", tags=["wishlist"])


@router.get("", response_model=list[ProductOut])
def list_wishlist(
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    """Return active wishlist products, newest additions first."""
    items = (
        db.query(WishlistItem)
        .join(Product, Product.id == WishlistItem.product_id)
        .options(
            selectinload(WishlistItem.product).selectinload(Product.images),
            selectinload(WishlistItem.product).selectinload(Product.variants),
        )
        .filter(
            WishlistItem.customer_id == customer.id,
            Product.active.is_(True),
        )
        .order_by(WishlistItem.created_at.desc(), WishlistItem.id.desc())
        .all()
    )
    return [item.product for item in items]


@router.post("", status_code=status.HTTP_200_OK)
def add_wishlist(
    payload: WishlistIn,
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    """Add a product idempotently and reject missing or inactive products."""
    product = (
        db.query(Product)
        .filter(Product.id == payload.product_id, Product.active.is_(True))
        .first()
    )
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found or unavailable",
        )

    existing = (
        db.query(WishlistItem)
        .filter(
            WishlistItem.customer_id == customer.id,
            WishlistItem.product_id == payload.product_id,
        )
        .first()
    )
    if existing:
        return {"ok": True, "added": False, "product_id": payload.product_id}

    db.add(WishlistItem(customer_id=customer.id, product_id=payload.product_id))
    try:
        db.commit()
    except IntegrityError:
        # A repeated parallel tap must remain harmless.
        db.rollback()
        return {"ok": True, "added": False, "product_id": payload.product_id}

    return {"ok": True, "added": True, "product_id": payload.product_id}


@router.delete("/{product_id}")
def remove_wishlist(
    product_id: int,
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    """Remove a product idempotently from the current customer's wishlist."""
    item = (
        db.query(WishlistItem)
        .filter(
            WishlistItem.customer_id == customer.id,
            WishlistItem.product_id == product_id,
        )
        .first()
    )
    if not item:
        return {"ok": True, "removed": False, "product_id": product_id}

    db.delete(item)
    db.commit()
    return {"ok": True, "removed": True, "product_id": product_id}
