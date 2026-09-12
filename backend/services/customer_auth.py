import hashlib
import hmac
from datetime import timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth_models import CustomerAuthEvent, CustomerSession, TelegramAuthAssertion
from ..config import get_settings
from ..database import utcnow_naive

_TELEGRAM_ASSERTION_RETENTION_HOURS = 24


class TelegramAssertionReplay(ValueError):
    """Raised when a signed Telegram bootstrap assertion was already consumed."""


def _auth_fingerprint(purpose: str, value: str) -> str:
    material = str(value or "")
    if not material:
        raise ValueError("Authentication identifier is missing")
    key = hashlib.sha256(
        f"flashin-customer-auth:{get_settings().jwt_secret}".encode("utf-8")
    ).digest()
    return hmac.new(
        key,
        f"{purpose}:{material}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def session_id_hash(session_identifier: str) -> str:
    return _auth_fingerprint("session-id", session_identifier)


def jti_hash(jti: str) -> str:
    return _auth_fingerprint("jti", jti)


def _telegram_assertion_material(parsed: dict) -> tuple[str, str]:
    canonical = "\n".join(
        f"{key}={value}" for key, value in sorted(parsed.items()) if key != "hash"
    )
    if not canonical:
        raise ValueError("Telegram assertion payload is empty")

    payload_hash = _auth_fingerprint("telegram-payload", canonical)
    query_id = str(parsed.get("query_id") or "").strip()
    if query_id:
        assertion_hash = _auth_fingerprint("telegram-query-id", query_id)
    else:
        # Some valid Telegram launch contexts do not include query_id. In that
        # case the exact signed payload becomes the one-time assertion key.
        # Missing user/auth_date cannot fall back and is rejected fail-closed.
        if not str(parsed.get("user") or "").strip() or not str(
            parsed.get("auth_date") or ""
        ).strip():
            raise ValueError("Telegram assertion lacks a replay-safe identity")
        assertion_hash = _auth_fingerprint("telegram-payload-fallback", canonical)
    return assertion_hash, payload_hash


def claim_telegram_assertion(db: Session, parsed: dict) -> TelegramAuthAssertion:
    assertion_hash, payload_hash = _telegram_assertion_material(parsed)
    try:
        auth_date = int(parsed.get("auth_date"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Telegram auth_date is invalid") from exc

    now = utcnow_naive()
    db.query(TelegramAuthAssertion).filter(
        TelegramAuthAssertion.retain_until < now
    ).delete(synchronize_session=False)

    row = TelegramAuthAssertion(
        assertion_hash=assertion_hash,
        payload_hash=payload_hash,
        auth_date=auth_date,
        consumed_at=now,
        retain_until=now + timedelta(hours=_TELEGRAM_ASSERTION_RETENTION_HOURS),
    )
    try:
        # SAVEPOINT keeps the outer authentication transaction usable when a
        # concurrent request loses the unique assertion claim race.
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise TelegramAssertionReplay("Telegram initData was already consumed") from exc
    return row


def register_customer_session(
    db: Session,
    *,
    customer_id: int,
    session_identifier: str,
    jti: str,
    expires_at,
) -> CustomerSession:
    row = CustomerSession(
        customer_id=customer_id,
        session_id_hash=session_id_hash(session_identifier),
        jti_hash=jti_hash(jti),
        expires_at=expires_at,
    )
    db.add(row)
    db.flush()
    db.add(
        CustomerAuthEvent(
            customer_id=customer_id,
            session_id=row.id,
            event_type="session.created",
        )
    )
    return row


def find_customer_session(
    db: Session,
    *,
    customer_id: int,
    session_identifier: str,
    jti: str,
) -> CustomerSession | None:
    return (
        db.query(CustomerSession)
        .filter(
            CustomerSession.customer_id == customer_id,
            CustomerSession.session_id_hash == session_id_hash(session_identifier),
            CustomerSession.jti_hash == jti_hash(jti),
        )
        .first()
    )


def revoke_customer_session(
    db: Session,
    *,
    customer_id: int,
    session_id: int,
    reason: str,
) -> tuple[CustomerSession | None, bool]:
    row = (
        db.query(CustomerSession)
        .filter(
            CustomerSession.id == session_id,
            CustomerSession.customer_id == customer_id,
        )
        .with_for_update()
        .first()
    )
    if row is None:
        return None, False
    if row.revoked_at is not None:
        return row, False

    row.revoked_at = utcnow_naive()
    row.revoke_reason = str(reason or "session_revoked")[:120]
    db.add(
        CustomerAuthEvent(
            customer_id=customer_id,
            session_id=row.id,
            event_type="session.revoked",
        )
    )
    return row, True


def revoke_customer_sessions(
    db: Session,
    *,
    customer_id: int,
    reason: str,
) -> int:
    rows = (
        db.query(CustomerSession)
        .filter(
            CustomerSession.customer_id == customer_id,
            CustomerSession.revoked_at.is_(None),
        )
        .with_for_update()
        .all()
    )
    now = utcnow_naive()
    for row in rows:
        row.revoked_at = now
        row.revoke_reason = str(reason or "all_sessions_revoked")[:120]
        db.add(
            CustomerAuthEvent(
                customer_id=customer_id,
                session_id=row.id,
                event_type="session.revoked",
            )
        )
    return len(rows)
