from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..middleware.rate_limit import _client_ip
from ..models import AdminPasswordReset, AdminTotpSecret, AdminUser
from ..schemas import TokenOut
from ..security import (
    create_admin_token,
    hash_password,
    password_needs_rehash,
    verify_password,
)
from ..services.admin_security import (
    create_admin_session,
    is_admin_ip_allowed,
    log_admin_login,
    revoke_admin_sessions,
    sha256,
    upgrade_totp_secret_encryption,
    verify_stored_totp,
)

router = APIRouter(prefix="/admin", tags=["admin-auth"])
_DUMMY_PASSWORD_HASH = hash_password("not-a-real-admin-password")
_COMMON_PASSWORDS = {
    "password",
    "password123",
    "admin",
    "admin123",
    "qwerty123",
    "change-me-now",
}


class AdminSessionLoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=1024)
    totp_code: str | None = Field(default=None, max_length=16)


class AdminPasswordResetConfirmIn(BaseModel):
    token: str = Field(min_length=32, max_length=512)
    new_password: str = Field(min_length=12, max_length=1024)


def _request_identity(request: Request) -> tuple[str, str]:
    settings = get_settings()
    trust_proxy_headers = settings.app_env.strip().lower() == "production"
    return (
        _client_ip(request, trust_proxy_headers=trust_proxy_headers),
        request.headers.get("user-agent", "")[:2000],
    )


def _validate_new_admin_password(password: str, email: str = "") -> None:
    lowered = password.lower()
    if lowered in _COMMON_PASSWORDS:
        raise HTTPException(status_code=400, detail="New password is too weak")
    classes = sum(
        (
            any(character.islower() for character in password),
            any(character.isupper() for character in password),
            any(character.isdigit() for character in password),
            any(not character.isalnum() for character in password),
        )
    )
    if classes < 3:
        raise HTTPException(
            status_code=400,
            detail="New password must use at least three character classes",
        )
    email_local = (email or "").split("@", 1)[0].strip().lower()
    if len(email_local) >= 4 and email_local in lowered:
        raise HTTPException(status_code=400, detail="New password must not contain the email name")


@router.post("/login", response_model=TokenOut)
def admin_session_login(
    payload: AdminSessionLoginIn,
    request: Request,
    db: Session = Depends(get_db),
):
    email = payload.email.strip().lower()
    ip_address, user_agent = _request_identity(request)

    if not is_admin_ip_allowed(db, ip_address):
        log_admin_login(
            db,
            email,
            None,
            False,
            "ip_not_allowed",
            ip_address,
            user_agent,
        )
        db.commit()
        raise HTTPException(status_code=403, detail="Admin access is not allowed")

    try:
        admin = (
            db.query(AdminUser)
            .filter(AdminUser.email == email, AdminUser.active.is_(True))
            .with_for_update()
            .first()
        )
        stored_hash = admin.password_hash if admin else _DUMMY_PASSWORD_HASH
        password_valid = verify_password(payload.password, stored_hash)
        if not admin or not password_valid:
            log_admin_login(
                db,
                email,
                admin.id if admin else None,
                False,
                "invalid_credentials",
                ip_address,
                user_agent,
            )
            db.commit()
            raise HTTPException(status_code=401, detail="Invalid admin credentials")

        totp = (
            db.query(AdminTotpSecret)
            .filter(AdminTotpSecret.admin_id == admin.id)
            .with_for_update()
            .first()
        )
        if totp and totp.enabled:
            try:
                totp_valid = verify_stored_totp(
                    admin.id,
                    totp.secret,
                    payload.totp_code or "",
                )
            except ValueError:
                totp_valid = False
            if not totp_valid:
                log_admin_login(
                    db,
                    email,
                    admin.id,
                    False,
                    "invalid_totp",
                    ip_address,
                    user_agent,
                )
                db.commit()
                raise HTTPException(status_code=401, detail="Invalid admin credentials")
            upgrade_totp_secret_encryption(totp)

        if password_needs_rehash(admin.password_hash):
            admin.password_hash = hash_password(payload.password)

        token = create_admin_token(admin.id, admin.role)
        create_admin_session(db, admin.id, token, ip_address, user_agent)
        log_admin_login(
            db,
            email,
            admin.id,
            True,
            "success",
            ip_address,
            user_agent,
        )
        db.commit()
        return TokenOut(access_token=token)
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise


@router.post("/password-reset/confirm")
def confirm_admin_password_reset(
    payload: AdminPasswordResetConfirmIn,
    request: Request,
    db: Session = Depends(get_db),
):
    ip_address, user_agent = _request_identity(request)
    if not is_admin_ip_allowed(db, ip_address):
        log_admin_login(
            db,
            "",
            None,
            False,
            "reset_ip_not_allowed",
            ip_address,
            user_agent,
        )
        db.commit()
        raise HTTPException(status_code=403, detail="Admin access is not allowed")

    now = datetime.utcnow()
    try:
        reset = (
            db.query(AdminPasswordReset)
            .filter(
                AdminPasswordReset.token_hash == sha256(payload.token),
                AdminPasswordReset.used.is_(False),
                AdminPasswordReset.expires_at > now,
            )
            .with_for_update()
            .first()
        )
        if not reset:
            log_admin_login(
                db,
                "",
                None,
                False,
                "invalid_or_expired_reset_token",
                ip_address,
                user_agent,
            )
            db.commit()
            raise HTTPException(status_code=400, detail="Reset token is invalid or expired")

        admin = (
            db.query(AdminUser)
            .filter(AdminUser.id == reset.admin_id, AdminUser.active.is_(True))
            .with_for_update()
            .first()
        )
        if not admin:
            reset.used = True
            db.commit()
            raise HTTPException(status_code=400, detail="Reset token is invalid or expired")

        _validate_new_admin_password(payload.new_password, admin.email)
        if verify_password(payload.new_password, admin.password_hash):
            raise HTTPException(status_code=400, detail="New password must be different")

        admin.password_hash = hash_password(payload.new_password)
        reset.used = True
        revoked = revoke_admin_sessions(db, admin.id)
        log_admin_login(
            db,
            admin.email,
            admin.id,
            True,
            f"password_reset_success_sessions_revoked_{revoked}",
            ip_address,
            user_agent,
        )
        db.commit()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise
