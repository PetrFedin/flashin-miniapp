import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth_models import CustomerSession
from ..database import get_db
from ..models import CrmProfile, Customer
from ..schemas import MeOut, TelegramAuthIn, TokenOut
from ..security import (
    CustomerAuthContext,
    bearer,
    get_current_customer,
    get_current_customer_context,
    issue_customer_session_token,
    resolve_customer_token,
    verify_telegram_init_data,
)
from ..services.customer_auth import (
    TelegramAssertionReplay,
    claim_telegram_assertion,
    revoke_customer_session,
    revoke_customer_sessions,
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


def _get_or_create_customer(
    db: Session,
    *,
    telegram_id: str,
    username: str,
    first_name: str,
    last_name: str,
) -> Customer:
    customer = (
        db.query(Customer)
        .filter(Customer.telegram_id == telegram_id)
        .with_for_update()
        .first()
    )
    if customer is None:
        candidate = Customer(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
        )
        try:
            # The savepoint lets distinct, concurrently valid Telegram
            # assertions for one customer converge on the same Customer row
            # without rolling back either assertion claim.
            with db.begin_nested():
                db.add(candidate)
                db.flush()
            customer = candidate
        except IntegrityError:
            customer = (
                db.query(Customer)
                .filter(Customer.telegram_id == telegram_id)
                .with_for_update()
                .first()
            )
            if customer is None:
                raise HTTPException(status_code=409, detail="Customer provisioning conflict")

    if username:
        customer.username = username
    if first_name:
        customer.first_name = first_name
    if last_name:
        customer.last_name = last_name
    return customer


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

    try:
        claim_telegram_assertion(db, parsed)
        customer = _get_or_create_customer(
            db,
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
        )
        _ensure_crm_profile(db, customer.id)
        token, _session = issue_customer_session_token(db, customer.id)
        db.commit()
        return TokenOut(access_token=token)
    except TelegramAssertionReplay:
        db.rollback()
        raise HTTPException(status_code=401, detail="Telegram initData already used")
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=401, detail="Telegram initData cannot be consumed") from exc
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


@router.get("/me", response_model=MeOut)
def me(customer: Customer = Depends(get_current_customer)):
    return customer


@router.post("/logout")
def logout(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
):
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Bearer token required")
    context = resolve_customer_token(db, credentials.credentials, require_active=False)
    try:
        _row, changed = revoke_customer_session(
            db,
            customer_id=context.customer.id,
            session_id=context.session.id,
            reason="logout",
        )
        db.commit()
        return {"ok": True, "idempotent": not changed}
    except Exception:
        db.rollback()
        raise


@router.get("/sessions")
def list_sessions(
    context: CustomerAuthContext = Depends(get_current_customer_context),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(CustomerSession)
        .filter(CustomerSession.customer_id == context.customer.id)
        .order_by(CustomerSession.created_at.desc(), CustomerSession.id.desc())
        .all()
    )
    return [
        {
            "id": row.id,
            "current": row.id == context.session.id,
            "created_at": row.created_at,
            "expires_at": row.expires_at,
            "revoked": row.revoked_at is not None,
        }
        for row in rows
    ]


@router.post("/sessions/revoke-all")
def revoke_all_sessions(
    context: CustomerAuthContext = Depends(get_current_customer_context),
    db: Session = Depends(get_db),
):
    try:
        count = revoke_customer_sessions(
            db,
            customer_id=context.customer.id,
            reason="customer_revoke_all",
        )
        db.commit()
        return {"ok": True, "revoked": count}
    except Exception:
        db.rollback()
        raise


@router.post("/sessions/{session_id}/revoke")
def revoke_one_session(
    session_id: int,
    context: CustomerAuthContext = Depends(get_current_customer_context),
    db: Session = Depends(get_db),
):
    if session_id <= 0:
        raise HTTPException(status_code=404, detail="Customer session not found")
    try:
        row, changed = revoke_customer_session(
            db,
            customer_id=context.customer.id,
            session_id=session_id,
            reason="customer_revoke_one",
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Customer session not found")
        db.commit()
        return {"ok": True, "idempotent": not changed}
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
