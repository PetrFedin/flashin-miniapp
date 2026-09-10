import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import CrmProfile, Customer
from ..schemas import MeOut, TelegramAuthIn, TokenOut
from ..security import bearer, get_current_customer, get_customer_token_payload, verify_telegram_init_data
from ..services.customer_auth_security import (
    consume_telegram_assertion,
    issue_customer_session_token,
    revoke_all_customer_sessions,
    revoke_customer_session,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _clean_profile_value(value: object, max_length: int) -> str:
    return str(value or "").strip()[:max_length]


def _ensure_crm_profile(db: Session, customer_id: int) -> CrmProfile:
    profile = (
        db.query(CrmProfile)
        .filter(CrmProfile.customer_id == customer_id)
        .with_for_update()
        .first()
    )
    if profile:
        return profile

    profile = CrmProfile(customer_id=customer_id, segment="new", loyalty_points=0)
    db.add(profile)
    db.flush()
    return profile


@router.post("/telegram", response_model=TokenOut)
def telegram_auth(payload: TelegramAuthIn, db: Session = Depends(get_db)):
    parsed = verify_telegram_init_data(payload.init_data)
    try:
        tg_user = json.loads(parsed.get("user", "{}"))
    except (TypeError, json.JSONDecodeError):
        raise HTTPException(status_code=401, detail="Invalid Telegram user payload")
    if not isinstance(tg_user, dict):
        raise HTTPException(status_code=401, detail="Invalid Telegram user payload")

    telegram_id = _clean_profile_value(tg_user.get("id"), 64)
    if not telegram_id or not telegram_id.isdigit():
        raise HTTPException(status_code=401, detail="Telegram user id missing")

    username = _clean_profile_value(tg_user.get("username"), 255)
    first_name = _clean_profile_value(tg_user.get("first_name"), 255)
    last_name = _clean_profile_value(tg_user.get("last_name"), 255)

    # Consume the signed bootstrap credential before any recoverable customer
    # provisioning transaction. A duplicate assertion therefore fails closed
    # and can never fall through the Customer/CRM IntegrityError recovery path.
    consume_telegram_assertion(parsed)

    access_token: str
    try:
        customer = (
            db.query(Customer)
            .filter(Customer.telegram_id == telegram_id)
            .with_for_update()
            .first()
        )
        if not customer:
            customer = Customer(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
            )
            db.add(customer)
            db.flush()
        else:
            if username:
                customer.username = username
            if first_name:
                customer.first_name = first_name
            if last_name:
                customer.last_name = last_name

        _ensure_crm_profile(db, customer.id)
        access_token = issue_customer_session_token(db, customer.id)
        db.commit()
    except IntegrityError:
        db.rollback()
        customer = db.query(Customer).filter(Customer.telegram_id == telegram_id).first()
        if not customer:
            raise HTTPException(status_code=409, detail="Customer provisioning conflict")
        try:
            _ensure_crm_profile(db, customer.id)
            access_token = issue_customer_session_token(db, customer.id)
            db.commit()
        except IntegrityError:
            db.rollback()
            if not db.query(CrmProfile).filter(CrmProfile.customer_id == customer.id).first():
                raise HTTPException(status_code=409, detail="CRM profile provisioning conflict")
            raise HTTPException(status_code=409, detail="Customer session provisioning conflict")
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise

    return TokenOut(access_token=access_token)


@router.get("/me", response_model=MeOut)
def me(customer: Customer = Depends(get_current_customer)):
    return customer


@router.post("/logout")
def logout(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    if not credentials:
        raise HTTPException(status_code=401, detail="Bearer token required")
    payload = get_customer_token_payload(credentials.credentials)
    try:
        revoke_customer_session(db, customer.id, str(payload.get("jti") or ""))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"ok": True}


@router.post("/logout-all")
def logout_all(
    customer: Customer = Depends(get_current_customer),
    db: Session = Depends(get_db),
):
    try:
        revoked = revoke_all_customer_sessions(db, customer.id)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"ok": True, "revoked_sessions": revoked}
