from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Customer, ProductVariant, RestockSubscription
from ..schemas import RestockSubscribeIn
from ..security import get_current_customer

router = APIRouter(prefix="/restock", tags=["restock"])


@router.post("/subscribe")
def subscribe(
    payload: RestockSubscribeIn,
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    try:
        variant = (
            db.query(ProductVariant)
            .filter(ProductVariant.id == payload.variant_id)
            .with_for_update()
            .first()
        )
        if not variant:
            raise HTTPException(status_code=404, detail="Variant not found")
        if variant.stock_qty < 0 or variant.reserved_qty < 0 or variant.reserved_qty > variant.stock_qty:
            raise HTTPException(status_code=409, detail="Inventory state is invalid")
        if variant.available_qty > 0:
            raise HTTPException(status_code=409, detail="Variant is already available")

        existing = (
            db.query(RestockSubscription)
            .filter(
                RestockSubscription.customer_id == customer.id,
                RestockSubscription.variant_id == variant.id,
            )
            .with_for_update()
            .first()
        )
        if existing:
            existing.active = True
            subscription = existing
        else:
            subscription = RestockSubscription(
                customer_id=customer.id,
                variant_id=variant.id,
                active=True,
            )
            db.add(subscription)
        db.commit()
        db.refresh(subscription)
        return {
            "ok": True,
            "subscription_id": subscription.id,
            "variant_id": variant.id,
            "active": subscription.active,
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
