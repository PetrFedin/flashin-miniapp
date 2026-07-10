from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import Customer, LoyaltyTransaction
from ..schemas import LoyaltyTransactionOut, ReferralCodeOut
from ..security import get_current_customer
from ..services.loyalty import ensure_referral_code

router = APIRouter(prefix="/loyalty", tags=["loyalty"])


@router.get("/transactions", response_model=list[LoyaltyTransactionOut])
def my_loyalty(customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    return db.query(LoyaltyTransaction).filter(LoyaltyTransaction.customer_id == customer.id).order_by(LoyaltyTransaction.created_at.desc()).limit(100).all()


@router.get("/referral-code", response_model=ReferralCodeOut)
def my_referral_code(customer: Customer = Depends(get_current_customer), db: Session = Depends(get_db)):
    return ensure_referral_code(db, customer.id)
