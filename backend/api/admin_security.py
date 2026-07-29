import ipaddress

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AdminIpAllowlist, AdminLoginEvent, AdminSession, AdminUser
from ..schemas import AdminIpAllowlistIn, AdminLoginEventOut
from ..security import get_current_admin
from ..services.admin_security import (
    consume_totp_counter,
    create_password_reset,
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
    target = db.query(AdminUser).filter(AdminUser.id == admin_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Admin not found")

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
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="CIDR rule already exists") from exc
    except Exception:
        db.rollback()
        raise
