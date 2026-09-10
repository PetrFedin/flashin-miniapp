import hashlib
import json
import secrets
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..customer_auth_models import CustomerSession, TelegramAuthConsumption
from ..database import SessionLocal, utcnow_naive


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def telegram_assertion_digest(parsed: dict) -> str:
    canonical = json.dumps(
        sorted((str(key), str(value)) for key, value in parsed.items()),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return _sha256(canonical)


def telegram_query_id_digest(parsed: dict) -> str | None:
    query_id = str(parsed.get("query_id") or "").strip()
    return _sha256(query_id) if query_id else None


def consume_telegram_assertion(parsed: dict) -> None:
    try:
        auth_date_epoch = int(parsed.get("auth_date"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Telegram auth_date is invalid") from exc

    row = TelegramAuthConsumption(
        assertion_digest=telegram_assertion_digest(parsed),
        query_id_digest=telegram_query_id_digest(parsed),
        auth_date_epoch=auth_date_epoch,
    )
    # This transaction is intentionally independent from Customer/CRM
    # provisioning. Once a signed Telegram assertion is consumed it must stay
    # consumed even if downstream provisioning fails and rolls back.
    with SessionLocal() as replay_db:
        try:
            replay_db.add(row)
            replay_db.commit()
        except IntegrityError as exc:
            replay_db.rollback()
            raise HTTPException(status_code=401, detail="Telegram initData already used") from exc


def token_id_hash(token_id: str) -> str:
    normalized = str(token_id or "").strip()
    if not normalized:
        raise ValueError("Customer session token id is required")
    return _sha256(normalized)


def issue_customer_session_token(db: Session, customer_id: int) -> str:
    if customer_id <= 0:
        raise ValueError("Customer id must be positive")
    settings = get_settings()
    now = utcnow_naive()
    token_id = secrets.token_urlsafe(24)
    expires_at = now + timedelta(minutes=settings.jwt_expire_minutes)
    db.add(
        CustomerSession(
            customer_id=customer_id,
            token_id_hash=token_id_hash(token_id),
            expires_at=expires_at,
        )
    )
    db.flush()

    # Import locally to keep the persistence service independent from the
    # FastAPI security module during module initialization.
    from ..security import create_access_token

    return create_access_token(customer_id, token_id=token_id)


def is_customer_session_active(db: Session, customer_id: int, token_id: str) -> bool:
    now = utcnow_naive()
    return (
        db.query(CustomerSession.id)
        .filter(
            CustomerSession.customer_id == customer_id,
            CustomerSession.token_id_hash == token_id_hash(token_id),
            CustomerSession.revoked_at.is_(None),
            CustomerSession.expires_at > now,
        )
        .first()
        is not None
    )


def revoke_customer_session(db: Session, customer_id: int, token_id: str) -> int:
    row = (
        db.query(CustomerSession)
        .filter(
            CustomerSession.customer_id == customer_id,
            CustomerSession.token_id_hash == token_id_hash(token_id),
        )
        .with_for_update()
        .first()
    )
    if not row or row.revoked_at is not None:
        return 0
    row.revoked_at = utcnow_naive()
    return 1


def revoke_all_customer_sessions(db: Session, customer_id: int) -> int:
    now = utcnow_naive()
    rows = (
        db.query(CustomerSession)
        .filter(
            CustomerSession.customer_id == customer_id,
            CustomerSession.revoked_at.is_(None),
        )
        .with_for_update()
        .all()
    )
    for row in rows:
        row.revoked_at = now
    return len(rows)
