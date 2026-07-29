from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..middleware.rate_limit import _client_ip
from ..models import AdminPasswordReset, AdminSession, AdminTotpSecret, AdminUser
from ..schemas import TokenOut
from ..security import (
    bearer,
    create_admin_mfa_setup_token,
    create_admin_token,
    get_current_admin,
    get_current_admin_mfa_setup,
    hash_password,
    password_needs_rehash,
    verify_password,
)
from ..services.admin_login_lockout import (
    acquire_admin_login_locks,
    admin_login_retry_after,
)
from ..services.admin_security import (
    consume_totp_counter,
    create_admin_session,
    generate_totp_secret,
    is_admin_ip_allowed,
    log_admin_login,
    revoke_admin_sessions,
    set_totp_secret,
    sha256,
    totp_provisioning_uri,
    upgrade_totp_secret_encryption,
    verify_stored_totp_counter,
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


class AdminLoginOut(BaseModel):
    access_token: str = ""
    token_type: str = "bearer"
    mfa_setup_required: bool = False
    setup_token: str = ""


class AdminMfaConfirmIn(BaseModel):
    code: str = Field(min_length=6, max_length=16)


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


def _require_allowed_admin_ip(db: Session, request: Request, admin: AdminUser | None = None) -> tuple[str, str]:
    ip_address, user_agent = _request_identity(request)
    if is_admin_ip_allowed(db, ip_address):
        return ip_address, user_agent
    log_admin_login(
        db,
        admin.email if admin else "",
        admin.id if admin else None,
        False,
        "ip_not_allowed",
        ip_address,
        user_agent,
    )
    db.commit()
    raise HTTPException(status_code=403, detail="Admin access is not allowed")


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


def _issue_admin_session(
    db: Session,
    admin: AdminUser,
    ip_address: str,
    user_agent: str,
) -> str:
    token = create_admin_token(admin.id, admin.role)
    create_admin_session(db, admin.id, token, ip_address, user_agent)
    return token


@router.post("/login", response_model=AdminLoginOut)
def admin_session_login(
    payload: AdminSessionLoginIn,
    request: Request,
    db: Session = Depends(get_db),
):
    settings = get_settings()
    email = payload.email.strip().lower()
    ip_address, user_agent = _require_allowed_admin_ip(db, request)

    try:
        acquire_admin_login_locks(db, email, ip_address)
        retry_after = admin_login_retry_after(db, email, ip_address)
        if retry_after > 0:
            log_admin_login(
                db,
                email,
                None,
                False,
                "login_locked",
                ip_address,
                user_agent,
            )
            db.commit()
            raise HTTPException(
                status_code=429,
                detail="Too many admin login attempts",
                headers={"Retry-After": str(retry_after)},
            )

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

        if password_needs_rehash(admin.password_hash):
            admin.password_hash = hash_password(payload.password)

        totp = (
            db.query(AdminTotpSecret)
            .filter(AdminTotpSecret.admin_id == admin.id)
            .with_for_update()
            .first()
        )
        if totp and totp.enabled:
            try:
                matched_counter = verify_stored_totp_counter(
                    admin.id,
                    totp.secret,
                    payload.totp_code or "",
                )
            except ValueError:
                matched_counter = None
            if matched_counter is None or not consume_totp_counter(db, admin.id, matched_counter):
                log_admin_login(
                    db,
                    email,
                    admin.id,
                    False,
                    "invalid_or_replayed_totp",
                    ip_address,
                    user_agent,
                )
                db.commit()
                raise HTTPException(status_code=401, detail="Invalid admin credentials")
            upgrade_totp_secret_encryption(totp)
        elif settings.admin_mfa_required:
            setup_token = create_admin_mfa_setup_token(admin)
            log_admin_login(
                db,
                email,
                admin.id,
                True,
                "mfa_setup_required",
                ip_address,
                user_agent,
            )
            db.commit()
            return AdminLoginOut(
                mfa_setup_required=True,
                setup_token=setup_token,
            )

        token = _issue_admin_session(db, admin, ip_address, user_agent)
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
        return AdminLoginOut(access_token=token)
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        raise


@router.post("/logout", status_code=204)
def admin_session_logout(
    response: Response,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Admin bearer token required")

    try:
        session = (
            db.query(AdminSession)
            .filter(
                AdminSession.admin_id == admin.id,
                AdminSession.session_token_hash == sha256(credentials.credentials),
                AdminSession.revoked.is_(False),
            )
            .with_for_update()
            .first()
        )
        if not session:
            raise HTTPException(status_code=401, detail="Admin session is revoked or unknown")
        session.revoked = True
        session.revoked_at = datetime.utcnow()
        log_admin_login(
            db,
            admin.email,
            admin.id,
            True,
            "logout",
            session.ip_address,
            session.user_agent,
        )
        db.commit()
        response.headers["Cache-Control"] = "no-store"
        return None
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


@router.post("/mfa/setup/start")
def start_admin_mfa_setup(
    request: Request,
    admin: AdminUser = Depends(get_current_admin_mfa_setup),
    db: Session = Depends(get_db),
):
    ip_address, user_agent = _require_allowed_admin_ip(db, request, admin)
    try:
        existing = (
            db.query(AdminTotpSecret)
            .filter(AdminTotpSecret.admin_id == admin.id)
            .with_for_update()
            .first()
        )
        if existing and existing.enabled:
            raise HTTPException(status_code=409, detail="MFA is already enabled")

        secret = generate_totp_secret()
        set_totp_secret(db, admin.id, secret, enabled=False)
        log_admin_login(
            db,
            admin.email,
            admin.id,
            True,
            "mfa_setup_started",
            ip_address,
            user_agent,
        )
        db.commit()
        return {
            "secret_once": secret,
            "otpauth_uri": totp_provisioning_uri(secret, admin.email),
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


@router.post("/mfa/setup/confirm", response_model=TokenOut)
def confirm_admin_mfa_setup(
    payload: AdminMfaConfirmIn,
    request: Request,
    admin: AdminUser = Depends(get_current_admin_mfa_setup),
    db: Session = Depends(get_db),
):
    ip_address, user_agent = _require_allowed_admin_ip(db, request, admin)
    try:
        totp = (
            db.query(AdminTotpSecret)
            .filter(AdminTotpSecret.admin_id == admin.id)
            .with_for_update()
            .first()
        )
        if not totp:
            raise HTTPException(status_code=409, detail="Start MFA setup first")
        if totp.enabled:
            raise HTTPException(status_code=409, detail="MFA is already enabled")

        try:
            matched_counter = verify_stored_totp_counter(
                admin.id,
                totp.secret,
                payload.code,
            )
        except ValueError:
            matched_counter = None
        if matched_counter is None or not consume_totp_counter(db, admin.id, matched_counter):
            log_admin_login(
                db,
                admin.email,
                admin.id,
                False,
                "mfa_setup_invalid_code",
                ip_address,
                user_agent,
            )
            db.commit()
            raise HTTPException(status_code=400, detail="Invalid authentication code")

        totp.enabled = True
        upgrade_totp_secret_encryption(totp)
        revoke_admin_sessions(db, admin.id)
        token = _issue_admin_session(db, admin, ip_address, user_agent)
        log_admin_login(
            db,
            admin.email,
            admin.id,
            True,
            "mfa_setup_completed",
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
