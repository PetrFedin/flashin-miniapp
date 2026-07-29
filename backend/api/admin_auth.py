from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import AdminTotpSecret, AdminUser
from ..schemas import TokenOut
from ..security import (
    create_admin_token,
    hash_password,
    password_needs_rehash,
    verify_password,
)
from ..middleware.rate_limit import _client_ip
from ..services.admin_security import (
    create_admin_session,
    is_admin_ip_allowed,
    log_admin_login,
    verify_totp,
)

router = APIRouter(prefix="/admin", tags=["admin-auth"])
_DUMMY_PASSWORD_HASH = hash_password("not-a-real-admin-password")


class AdminSessionLoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=1024)
    totp_code: str | None = Field(default=None, max_length=16)


@router.post("/login", response_model=TokenOut)
def admin_session_login(
    payload: AdminSessionLoginIn,
    request: Request,
    db: Session = Depends(get_db),
):
    settings = get_settings()
    email = payload.email.strip().lower()
    trust_proxy_headers = settings.app_env.strip().lower() == "production"
    ip_address = _client_ip(request, trust_proxy_headers=trust_proxy_headers)
    user_agent = request.headers.get("user-agent", "")[:2000]

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
                totp_valid = verify_totp(totp.secret, payload.totp_code or "")
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
