from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import CartItem, Customer, ProductVariant
from ..security import get_current_customer
from .cart import (
    _MAX_VARIANT_QUANTITY,
    _load_cart,
    _lock_cart,
    get_or_create_cart,
    serialize_cart,
)

router = APIRouter(prefix="/cart", tags=["cart"])


@router.patch("/items/{item_id}")
def update_cart_item_quantity(
    item_id: int,
    quantity: int = Query(ge=1, le=_MAX_VARIANT_QUANTITY),
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    cart = get_or_create_cart(db, customer)
    try:
        _lock_cart(db, cart.id)
        item = (
            db.query(CartItem)
            .filter(CartItem.id == item_id, CartItem.cart_id == cart.id)
            .with_for_update()
            .first()
        )
        if not item:
            raise HTTPException(status_code=404, detail="Cart item not found")

        variant = (
            db.query(ProductVariant)
            .filter(ProductVariant.id == item.variant_id, ProductVariant.product_id == item.product_id)
            .with_for_update()
            .first()
        )
        if not variant:
            raise HTTPException(status_code=409, detail="Cart item has a broken product/variant link")
        if variant.stock_qty < 0 or variant.reserved_qty < 0 or variant.reserved_qty > variant.stock_qty:
            raise HTTPException(status_code=409, detail="Inventory state is invalid")
        if variant.available_qty < quantity:
            raise HTTPException(status_code=409, detail="Not enough stock available")

        item.quantity = quantity
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise

    return serialize_cart(_load_cart(db, cart.id))
