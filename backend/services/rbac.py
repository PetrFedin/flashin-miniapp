from fastapi import HTTPException
from sqlalchemy.orm import Session
from ..models import AdminRolePermission, AdminUser


DEFAULT_PERMISSIONS = {
    "owner": {"*"},
    "manager": {"products.read", "products.write", "orders.read", "orders.write", "promo.write", "support.write"},
    "support": {"orders.read", "support.write", "customers.read"},
    "warehouse": {"products.read", "inventory.write", "orders.read"},
}


def has_permission(db: Session, admin: AdminUser, permission: str) -> bool:
    if admin.role == "owner":
        return True
    configured = db.query(AdminRolePermission).filter(AdminRolePermission.role == admin.role).all()
    permissions = {p.permission for p in configured} or DEFAULT_PERMISSIONS.get(admin.role, set())
    return "*" in permissions or permission in permissions


def require_permission(db: Session, admin: AdminUser, permission: str) -> None:
    if not has_permission(db, admin, permission):
        raise HTTPException(status_code=403, detail=f"Missing permission: {permission}")
