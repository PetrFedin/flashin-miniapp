import hashlib
import ipaddress
import secrets
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from ..models import AdminIpAllowlist, AdminLoginEvent, AdminPasswordReset, AdminSession, AdminTotpSecret, AdminUser


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def log_admin_login(db: Session, email: str, admin_id: int | None, success: bool, reason: str, ip: str = "", user_agent: str = "") -> None:
    db.add(AdminLoginEvent(admin_id=admin_id, email=email, success=success, reason=reason, ip_address=ip, user_agent=user_agent))


def is_admin_ip_allowed(db: Session, ip: str) -> bool:
    rules = db.query(AdminIpAllowlist).filter(AdminIpAllowlist.active == True).all()
    if not rules:
        return True
    try:
        addr = ipaddress.ip_address(ip)
        return any(addr in ipaddress.ip_network(r.cidr, strict=False) for r in rules)
    except Exception:
        return False


def create_admin_session(db: Session, admin_id: int, token: str, ip: str = "", user_agent: str = "") -> None:
    db.add(AdminSession(admin_id=admin_id, session_token_hash=sha256(token), ip_address=ip, user_agent=user_agent))


def revoke_admin_sessions(db: Session, admin_id: int) -> int:
    rows = db.query(AdminSession).filter(AdminSession.admin_id == admin_id, AdminSession.revoked == False).all()
    for row in rows:
        row.revoked = True
        row.revoked_at = datetime.utcnow()
    return len(rows)


def create_password_reset(db: Session, admin: AdminUser) -> str:
    token = secrets.token_urlsafe(32)
    db.add(AdminPasswordReset(admin_id=admin.id, token_hash=sha256(token), expires_at=datetime.utcnow() + timedelta(hours=1)))
    return token


def set_totp_secret(db: Session, admin_id: int, secret: str, enabled: bool = False) -> AdminTotpSecret:
    row = db.query(AdminTotpSecret).filter(AdminTotpSecret.admin_id == admin_id).first()
    if not row:
        row = AdminTotpSecret(admin_id=admin_id, secret=secret, enabled=enabled)
        db.add(row)
    else:
        row.secret = secret
        row.enabled = enabled
    return row
