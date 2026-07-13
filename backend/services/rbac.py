from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import AdminRolePermission, AdminUser


ROLE_ALIASES = {
    "owner": "superadmin",  # backwards compatibility for existing installations
}

DEFAULT_PERMISSIONS = {
    "superadmin": {"*"},
    "admin": {
        "products.read",
        "products.write",
        "products.archive",
        "prices.write",
        "inventory.write",
        "orders.read",
        "orders.write",
        "promo.read",
        "promo.write",
        "customers.read",
        "support.write",
        "moysklad.read",
        "moysklad.sync",
        "analytics.read",
    },
    "catalog_manager": {
        "products.read",
        "products.write",
        "products.archive",
        "prices.write",
        "inventory.write",
        "moysklad.read",
        "moysklad.sync",
    },
    "warehouse": {
        "products.read",
        "inventory.write",
        "orders.read",
        "moysklad.read",
        "moysklad.sync",
    },
    "support": {"orders.read", "support.write", "customers.read"},
    "marketing": {
        "products.read",
        "promo.read",
        "promo.write",
        "customers.read",
        "analytics.read",
    },
    # Kept for existing users created by older releases.
    "manager": {
        "products.read",
        "products.write",
        "products.archive",
        "prices.write",
        "inventory.write",
        "orders.read",
        "orders.write",
        "promo.read",
        "promo.write",
        "support.write",
        "customers.read",
    },
}

MANAGEABLE_ROLES = {
    "superadmin",
    "admin",
    "catalog_manager",
    "warehouse",
    "support",
    "marketing",
}


def normalize_role(role: str) -> str:
    return ROLE_ALIASES.get(role, role)


def has_permission(db: Session, admin: AdminUser, permission: str) -> bool:
    role = normalize_role(admin.role)
    if role == "superadmin":
        return True
    configured = db.query(AdminRolePermission).filter(AdminRolePermission.role == role).all()
    permissions = {row.permission for row in configured} or DEFAULT_PERMISSIONS.get(role, set())
    return "*" in permissions or permission in permissions


def require_permission(db: Session, admin: AdminUser, permission: str) -> None:
    if not admin.active:
        raise HTTPException(status_code=403, detail="Administrator account is disabled")
    if not has_permission(db, admin, permission):
        raise HTTPException(status_code=403, detail=f"Missing permission: {permission}")


def require_superadmin(admin: AdminUser) -> None:
    if normalize_role(admin.role) != "superadmin":
        raise HTTPException(status_code=403, detail="Superadmin access required")
