import hashlib
import math
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import AdminLoginEvent

_FAILURE_REASONS = {
    "invalid_credentials",
    "invalid_or_replayed_totp",
    "mfa_setup_invalid_code",
}
_RESET_REASONS = {
    "success",
    "mfa_setup_required",
    "mfa_setup_completed",
}


def utcnow() -> datetime:
    return datetime.utcnow()


def _normalized_email(email: str) -> str:
    return (email or "").strip().lower()[:255]


def _normalized_ip(ip_address: str) -> str:
    return (ip_address or "").strip()[:120]


def _advisory_lock_key(scope: str) -> int:
    digest = hashlib.sha256(scope.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def acquire_admin_login_locks(db: Session, email: str, ip_address: str) -> None:
    """Serialize authentication decisions for the same email and source IP.

    PostgreSQL transaction-level advisory locks are held until the login
    transaction commits or rolls back. SQLite and other test databases skip
    this production-specific primitive while exercising the same policy.
    """
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return

    scopes = {
        f"admin-login:email:{_normalized_email(email)}",
        f"admin-login:ip:{_normalized_ip(ip_address)}",
    }
    for scope in sorted(scopes):
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": _advisory_lock_key(scope)},
        )


def _scope_retry_after(
    db: Session,
    *,
    field,
    value: str,
    now: datetime,
    max_failures: int,
    failure_window_minutes: int,
    lockout_minutes: int,
) -> int:
    if not value:
        return 0

    cutoff = now - timedelta(minutes=failure_window_minutes)
    latest_reset = (
        db.query(AdminLoginEvent)
        .filter(
            field == value,
            AdminLoginEvent.success.is_(True),
            AdminLoginEvent.reason.in_(_RESET_REASONS),
            AdminLoginEvent.created_at >= cutoff,
        )
        .order_by(AdminLoginEvent.created_at.desc(), AdminLoginEvent.id.desc())
        .first()
    )
    if latest_reset and latest_reset.created_at > cutoff:
        cutoff = latest_reset.created_at

    failures = (
        db.query(AdminLoginEvent)
        .filter(
            field == value,
            AdminLoginEvent.success.is_(False),
            AdminLoginEvent.reason.in_(_FAILURE_REASONS),
            AdminLoginEvent.created_at > cutoff,
        )
        .order_by(AdminLoginEvent.created_at.desc(), AdminLoginEvent.id.desc())
        .limit(max_failures)
        .all()
    )
    if len(failures) < max_failures:
        return 0

    locked_until = failures[0].created_at + timedelta(minutes=lockout_minutes)
    remaining = (locked_until - now).total_seconds()
    return max(0, math.ceil(remaining))


def admin_login_retry_after(
    db: Session,
    email: str,
    ip_address: str,
    *,
    now: datetime | None = None,
) -> int:
    settings = get_settings()
    check_time = now or utcnow()
    common = {
        "db": db,
        "now": check_time,
        "max_failures": settings.admin_login_max_failures,
        "failure_window_minutes": settings.admin_login_failure_window_minutes,
        "lockout_minutes": settings.admin_login_lockout_minutes,
    }
    email_retry = _scope_retry_after(
        field=AdminLoginEvent.email,
        value=_normalized_email(email),
        **common,
    )
    ip_retry = _scope_retry_after(
        field=AdminLoginEvent.ip_address,
        value=_normalized_ip(ip_address),
        **common,
    )
    return max(email_retry, ip_retry)
