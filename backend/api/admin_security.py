import ipaddress

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..middleware.rate_limit import _client_ip
from ..models import AdminIpAllowlist, AdminLoginEvent, AdminSession, AdminUser
from ..schemas import AdminIpAllowlistIn, AdminLoginEventOut
from ..security import get_current_admin
from ..services.admin_security import (
    consume_totp_counter,
    create_password_reset,
    is_admin_ip_allowed,
    match_totp_counter,
    revoke_admin_sessions,
    set_totp_secret,
)
from ..services.audit import log_admin_action
from ..services.rbac import require_permission

router = APIRouter(prefix="/admin-security", tags=["admin-security"])


class AdminTotpIn(BaseModel):
    secret: str = Field(min_length=16, max_length=128)
    enabled: bool = False
    verification_code: str | None = Field(default=None, min_length=6, max_length=8)


class AdminIpAllowlistStateIn(BaseModel):
    active: bool


def _production_environment() -> bool:
    return get_settings().app_env.strip().lower() == "production"


def _production_admin_mfa_required() -> bool:
    return _production_environment()


def _admin_request_ip(request: Request) -> str:
    return _client_ip(request, trust_proxy_headers=_production_environment())


def _active_ip_rules_for_update(db: Session) -> list[AdminIpAllowlist]:
    return (
        db.query(AdminIpAllowlist)
        .filter(AdminIpAllowlist.active.is_(True))
        .with_for_update()
        .all()
    )


def _enforce_safe_ip_allowlist_result(
    db: Session,
    request: Request,
    *,
    require_active_rule: bool,
) -> None:
    active_rules = _active_ip_rules_for_update(db)
    if require_active_rule and not active_rules:
        raise HTTPException(
            status_code=409,
            detail="Production admin IP allowlist cannot be emptied via API",
        )
    if active_rules and not is_admin_ip_allowed(db, _admin_request_ip(request)):
        raise HTTPException(
            status_code=409,
            detail="IP allowlist change would lock out the current administrator",
        )


@router.get("/login-events", response_model=list[AdminLoginEventOut])
def login_events(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "security.read")
    return db.query(AdminLoginEvent).order_by(AdminLoginEvent.created_at.desc()).limit(300).all()


@router.get("/sessions")
def sessions(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "security.read")
    rows = db.query(AdminSession).order_by(AdminSession.created_at.desc()).limit(300).all()
    return [
        {
            "id": row.id,
            "admin_id": row.admin_id,
            "revoked": row.revoked,
            "ip_address": row.ip_address,
            "created_at": row.created_at,
            "revoked_at": row.revoked_at,
        }
        for row in rows
    ]


@router.post("/sessions/revoke/{admin_id}")
def revoke_sessions(
    admin_id: int,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "security.write")
    target = db.query(AdminUser).filter(AdminUser.id == admin_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Admin not found")
    try:
        count = revoke_admin_sessions(db, admin_id)
        log_admin_action(
            db,
            admin,
            "admin.sessions.revoke",
            "admin_user",
            admin_id,
            {"revoked": count},
        )
        db.commit()
        return {"revoked": count}
    except Exception:
        db.rollback()
        raise


@router.post("/password-reset/{admin_id}")
def password_reset(
    admin_id: int,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "security.write")
    target = db.query(AdminUser).filter(AdminUser.id == admin_id).with_for_update().first()
    if not target:
        raise HTTPException(status_code=404, detail="Admin not found")
    try:
        token = create_password_reset(db, target)
        log_admin_action(
            db,
            admin,
            "admin.password_reset.create",
            "admin_user",
            target.id,
            {},
        )
        db.commit()
        return {"reset_token_once": token}
    except Exception:
        db.rollback()
        raise


@router.post("/totp/{admin_id}")
def configure_totp(
    admin_id: int,
    payload: AdminTotpIn,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "security.write")
    # Serialize enrollment/rotation against login, which locks the same admin row
    # before consuming a TOTP counter.
    target = (
        db.query(AdminUser)
        .filter(AdminUser.id == admin_id)
        .with_for_update()
        .first()
    )
    if not target:
        raise HTTPException(status_code=404, detail="Admin not found")
    if _production_admin_mfa_required() and target.active and not payload.enabled:
        raise HTTPException(
            status_code=409,
            detail="TOTP cannot be disabled for an active administrator in production",
        )

    matched_counter = None
    if payload.enabled:
        try:
            matched_counter = match_totp_counter(
                payload.secret,
                payload.verification_code or "",
            )
        except ValueError:
            matched_counter = None
        if matched_counter is None:
            raise HTTPException(status_code=400, detail="Valid TOTP verification code is required")

    try:
        row = set_totp_secret(db, admin_id, payload.secret, payload.enabled)
        # The verification code used to enable/rotate MFA is already a successful
        # authentication factor and must not remain reusable for the next login.
        if matched_counter is not None and not consume_totp_counter(db, admin_id, matched_counter):
            raise HTTPException(status_code=409, detail="TOTP verification code was already used")
        log_admin_action(
            db,
            admin,
            "admin.totp.configure",
            "admin_user",
            admin_id,
            {"enabled": row.enabled},
        )
        db.commit()
        return {"ok": True, "enabled": row.enabled}
    except HTTPException:
        db.rollback()
        raise
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise


@router.get("/ip-allowlist")
def ip_allowlist(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "security.read")
    return db.query(AdminIpAllowlist).order_by(AdminIpAllowlist.id.desc()).all()


@router.post("/ip-allowlist")
def add_ip_rule(
    payload: AdminIpAllowlistIn,
    request: Request,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "security.write")
    try:
        network = ipaddress.ip_network(payload.cidr.strip(), strict=False)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid CIDR network") from exc

    try:
        row = AdminIpAllowlist(
            cidr=str(network),
            description=(payload.description or "").strip()[:2000],
            active=payload.active,
        )
        db.add(row)
        db.flush()
        if row.active:
            _enforce_safe_ip_allowlist_result(
                db,
                request,
                require_active_rule=False,
            )
        log_admin_action(
            db,
            admin,
            "admin.ip_allowlist.create",
            "admin_ip_allowlist",
            row.id,
            {"cidr": row.cidr, "active": row.active},
        )
        db.commit()
        db.refresh(row)
        return row
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="CIDR rule already exists") from exc
    except Exception:
        db.rollback()
        raise


@router.patch("/ip-allowlist/{rule_id}/state")
def set_ip_rule_state(
    rule_id: int,
    payload: AdminIpAllowlistStateIn,
    request: Request,
    admin=Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    require_permission(db, admin, "security.write")
    try:
        row = (
            db.query(AdminIpAllowlist)
            .filter(AdminIpAllowlist.id == rule_id)
            .with_for_update()
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="IP allowlist rule not found")

        before_active = bool(row.active)
        row.active = payload.active
        db.flush()
        _enforce_safe_ip_allowlist_result(
            db,
            request,
            require_active_rule=_production_environment(),
        )
        log_admin_action(
            db,
            admin,
            "admin.ip_allowlist.state",
            "admin_ip_allowlist",
            row.id,
            {
                "cidr": row.cidr,
                "before_active": before_active,
                "after_active": bool(row.active),
            },
        )
        db.commit()
        db.refresh(row)
        return row
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
