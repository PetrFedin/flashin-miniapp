from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from ..database import get_db
from ..models import AdminIpAllowlist, AdminLoginEvent, AdminSession, AdminTotpSecret, AdminUser
from ..schemas import AdminIpAllowlistIn, AdminLoginEventOut
from ..security import get_current_admin
from ..services.admin_security import create_password_reset, revoke_admin_sessions, set_totp_secret
from ..services.rbac import require_permission

router = APIRouter(prefix="/admin-security", tags=["admin-security"])


@router.get("/login-events", response_model=list[AdminLoginEventOut])
def login_events(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "admin_security.read")
    return db.query(AdminLoginEvent).order_by(AdminLoginEvent.created_at.desc()).limit(300).all()


@router.get("/sessions")
def sessions(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "admin_security.read")
    rows = db.query(AdminSession).order_by(AdminSession.created_at.desc()).limit(300).all()
    return [{"id": r.id, "admin_id": r.admin_id, "revoked": r.revoked, "ip_address": r.ip_address, "created_at": r.created_at} for r in rows]


@router.post("/sessions/revoke/{admin_id}")
def revoke_sessions(admin_id: int, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "admin_security.write")
    count = revoke_admin_sessions(db, admin_id)
    db.commit()
    return {"revoked": count}


@router.post("/password-reset/{admin_id}")
def password_reset(admin_id: int, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "admin_security.write")
    target = db.query(AdminUser).filter(AdminUser.id == admin_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="Admin not found")
    token = create_password_reset(db, target)
    db.commit()
    return {"reset_token_once": token}


@router.post("/totp/{admin_id}")
def set_totp(admin_id: int, secret: str, enabled: bool = False, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "admin_security.write")
    row = set_totp_secret(db, admin_id, secret, enabled)
    db.commit()
    return {"ok": True, "enabled": row.enabled}


@router.get("/ip-allowlist")
def ip_allowlist(admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "admin_security.read")
    return db.query(AdminIpAllowlist).order_by(AdminIpAllowlist.id.desc()).all()


@router.post("/ip-allowlist")
def add_ip_rule(payload: AdminIpAllowlistIn, admin=Depends(get_current_admin), db: Session = Depends(get_db)):
    require_permission(db, admin, "admin_security.write")
    row = AdminIpAllowlist(cidr=payload.cidr, description=payload.description, active=payload.active)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
