import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import Customer
from ..schemas import MeOut, TelegramAuthIn, TokenOut
from ..security import create_access_token, get_current_customer, verify_telegram_init_data

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/telegram", response_model=TokenOut)
def telegram_auth(payload: TelegramAuthIn, db: Session = Depends(get_db)):
    parsed = verify_telegram_init_data(payload.init_data)
    try:
        tg_user = json.loads(parsed.get("user", "{}"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=401, detail="Invalid Telegram user payload")

    telegram_id = str(tg_user.get("id", ""))
    if not telegram_id:
        raise HTTPException(status_code=401, detail="Telegram user id missing")

    customer = db.query(Customer).filter(Customer.telegram_id == telegram_id).first()
    if not customer:
        customer = Customer(
            telegram_id=telegram_id,
            username=tg_user.get("username", "") or "",
            first_name=tg_user.get("first_name", "") or "",
            last_name=tg_user.get("last_name", "") or "",
        )
        db.add(customer)
        db.commit()
        db.refresh(customer)
    else:
        customer.username = tg_user.get("username", customer.username) or ""
        customer.first_name = tg_user.get("first_name", customer.first_name) or ""
        customer.last_name = tg_user.get("last_name", customer.last_name) or ""
        db.commit()

    return TokenOut(access_token=create_access_token(customer.id))


@router.get("/me", response_model=MeOut)
def me(customer: Customer = Depends(get_current_customer)):
    return customer
