from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import CrmProfile, Customer
from ..schemas import CustomerProfileOut
from ..security import get_current_customer
from ..services.loyalty import ensure_referral_code

router = APIRouter(prefix="/profile", tags=["profile"])


@router.get("", response_model=CustomerProfileOut)
def my_profile(customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    try:
        crm = db.query(CrmProfile).filter(CrmProfile.customer_id == customer.id).first()
        ref = ensure_referral_code(db, customer.id)
        db.commit()
        db.refresh(ref)
        if crm:
            db.refresh(crm)
    except Exception:
        db.rollback()
        raise

    return CustomerProfileOut(
        customer={
            "id": customer.id,
            "telegram_id": customer.telegram_id,
            "username": customer.username,
            "first_name": customer.first_name,
            "phone": customer.phone,
        },
        crm={
            "segment": crm.segment,
            "orders_count": crm.orders_count,
            "total_spent": crm.total_spent,
            "average_order_value": crm.average_order_value,
            "vip": crm.vip,
        } if crm else None,
        referral_code=ref.code,
        loyalty_points=crm.loyalty_points if crm else 0,
    )
