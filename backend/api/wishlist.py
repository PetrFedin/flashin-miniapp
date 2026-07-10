from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, joinedload
from ..database import get_db
from ..models import Customer, WishlistItem
from ..schemas import ProductOut, WishlistIn
from ..security import get_current_customer

router = APIRouter(prefix="/wishlist", tags=["wishlist"])


@router.get("", response_model=list[ProductOut])
def list_wishlist(customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    items = db.query(WishlistItem).options(joinedload(WishlistItem.product)).filter(WishlistItem.customer_id == customer.id).all()
    return [i.product for i in items]


@router.post("")
def add_wishlist(payload: WishlistIn, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    exists = db.query(WishlistItem).filter(WishlistItem.customer_id == customer.id, WishlistItem.product_id == payload.product_id).first()
    if not exists:
        db.add(WishlistItem(customer_id=customer.id, product_id=payload.product_id))
        db.commit()
    return {"ok": True}


@router.delete("/{product_id}")
def remove_wishlist(product_id: int, customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    item = db.query(WishlistItem).filter(WishlistItem.customer_id == customer.id, WishlistItem.product_id == product_id).first()
    if item:
        db.delete(item)
        db.commit()
    return {"ok": True}
