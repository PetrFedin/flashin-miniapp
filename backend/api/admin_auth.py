from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db, utcnow_naive
from ..middleware.rate_limit import _client_ip
from ..models import AdminPasswordReset, AdminSession, AdminTotpSecret, AdminUser
from ..schemas import TokenOut
from ..security import (
    bearer,
    create_admin_token,
    get_current_admin,
    hash_password,
    password_needs_rehash,
    verify_password,
)
from ..services.admin_password_policy import validate_admin_password
from ..services.admin_security import (
    consume_totp_counter,
    create_admin_session,
    is_admin_ip_allowed,
    log_admin_login,
    match_stored_totp_counter,
    revoke_admin_sessions,
    sha256,
    upgrade_totp_secret_encryption,
)
from ..services.rbac import effective_permissions

router = APIRouter(prefix="/admin", tags=["admin-auth"])
_DUMMY_PASSWORD_HASH = hash_password("not-a-real-admin-password")


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


def _production_admin_mfa_required() -> bool:
    return get_settings().app_env.strip().lower() == "production"


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
        # This row lock is the serialization anchor for password + MFA state.
        # Two concurrent login attempts for the same administrator cannot both
        # consume the same TOTP counter before commit.
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
        if _production_admin_mfa_required() and (totp is None or not totp.enabled):
            log_admin_login(
                db,
                email,
                admin.id,
                False,
                "mfa_not_enrolled",
                ip_address,
                user_agent,
            )
            db.commit()
            raise HTTPException(
                status_code=403,
                detail="Admin MFA enrollment is required",
            )

        if totp and totp.enabled:
            try:
                totp_counter = match_stored_totp_counter(
                    admin.id,
                    totp.secret,
                    payload.totp_code or "",
                )
            except ValueError:
                totp_counter = None
            if totp_counter is None:
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
            if not consume_totp_counter(db, admin.id, totp_counter):
                log_admin_login(
                    db,
                    email,
                    admin.id,
                    False,
                    "totp_replay",
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
            admin.email,
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


@router.get("/session")
def current_admin_session(
    response: Response,
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Return only identity and the effective permissions used by RBAC.

    This is deliberately a no-store projection: it contains no token, session
    hash, TOTP state, IP data or role-configuration internals.
    """

    permissions = effective_permissions(db, admin)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return {
        "id": int(admin.id),
        "email": str(admin.email),
        "role": str(admin.role),
        "all_access": "*" in permissions,
        "permissions": sorted(permission for permission in permissions if permission != "*"),
    }


@router.post("/logout", status_code=204)
def admin_session_logout(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    admin: AdminUser = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Revoke exactly the bearer-backed administrator session being logged out."""

    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Admin authentication required")

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
        if session is None:
            raise HTTPException(status_code=401, detail="Admin session is not active")

        session.revoked = True
        session.revoked_at = utcnow_naive()
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
        return Response(
            status_code=204,
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
            },
        )
    except HTTPException:
        db.rollback()
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

    now = utcnow_naive()
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

        try:
            validate_admin_password(payload.new_password, admin.email)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
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
