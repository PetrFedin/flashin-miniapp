from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import AdminRolePermission, AdminUser


ROLE_ALIASES = {
    "owner": "superadmin",  # backwards compatibility for existing installations
}

APPOINTMENT_OPERATIONS = {
    "appointments.read",
    "appointments.write",
    "appointments.message",
}

APPOINTMENT_ADMINISTRATION = APPOINTMENT_OPERATIONS | {
    "appointments.manage_locations",
    "appointments.analytics",
}

DEFAULT_PERMISSIONS = {
    "superadmin": {"*"},
    "admin": {
        "products.read",
        "products.write",
        "products.archive",
        "prices.write",
        "inventory.read",
        "inventory.write",
        "orders.read",
        "orders.write",
        "promo.read",
        "promo.write",
        "customers.read",
        "crm.read",
        "crm.write",
        "support.read",
        "support.write",
        "notifications.read",
        "campaigns.read",
        "campaigns.write",
        "moysklad.read",
        "moysklad.write",
        "moysklad.sync",
        "analytics.read",
        "audit.read",
        "delivery.read",
        "delivery.write",
        "diagnostics.read",
        "fulfillment.read",
        "fulfillment.write",
        "media.write",
        "operations.read",
        "operations.write",
        "payments.reconcile.read",
        "payments.reconcile.write",
        "privacy.read",
        "privacy.write",
        "refunds.write",
        "webhooks.read",
        "webhooks.write",
        "feature_flags.write",
        "remote_config.write",
        "cms.write",
        "events.read",
        *APPOINTMENT_ADMINISTRATION,
    },
    "catalog_manager": {
        "products.read",
        "products.write",
        "products.archive",
        "prices.write",
        "inventory.read",
        "inventory.write",
        "moysklad.read",
        "moysklad.write",
        "moysklad.sync",
        "media.write",
        "cms.write",
    },
    "warehouse": {
        "products.read",
        "inventory.read",
        "inventory.write",
        "orders.read",
        "moysklad.read",
        "moysklad.sync",
        "delivery.read",
        "fulfillment.read",
        "operations.read",
    },
    "support": {
        "orders.read",
        "support.read",
        "support.write",
        "customers.read",
        "crm.read",
        "notifications.read",
        "privacy.read",
        *APPOINTMENT_OPERATIONS,
    },
    "marketing": {
        "products.read",
        "promo.read",
        "promo.write",
        "customers.read",
        "crm.read",
        "crm.write",
        "analytics.read",
        "notifications.read",
        "appointments.analytics",
    },
    "showroom_manager": {
        "products.read",
        "inventory.read",
        "orders.read",
        "customers.read",
        "crm.read",
        "crm.write",
        "notifications.read",
        *APPOINTMENT_ADMINISTRATION,
    },
    "clienteling": {
        "products.read",
        "inventory.read",
        "orders.read",
        "customers.read",
        "crm.read",
        "crm.write",
        "notifications.read",
        *APPOINTMENT_OPERATIONS,
    },
    "stylist": {
        "products.read",
        "inventory.read",
        "customers.read",
        "crm.read",
        "notifications.read",
        *APPOINTMENT_OPERATIONS,
    },
    # Kept for existing users created by older releases.
    "manager": {
        "products.read",
        "products.write",
        "products.archive",
        "prices.write",
        "inventory.read",
        "inventory.write",
        "orders.read",
        "orders.write",
        "promo.read",
        "promo.write",
        "crm.read",
        "crm.write",
        "support.read",
        "support.write",
        "customers.read",
        "notifications.read",
        "campaigns.read",
        "campaigns.write",
        "moysklad.read",
        "moysklad.write",
        "moysklad.sync",
        "analytics.read",
        "audit.read",
        "delivery.read",
        "delivery.write",
        "diagnostics.read",
        "fulfillment.read",
        "fulfillment.write",
        "media.write",
        "operations.read",
        "operations.write",
        "payments.reconcile.read",
        "payments.reconcile.write",
        "privacy.read",
        "privacy.write",
        "refunds.write",
        "webhooks.read",
        "webhooks.write",
        "feature_flags.write",
        "remote_config.write",
        "cms.write",
        "events.read",
        *APPOINTMENT_ADMINISTRATION,
    },
}

MANAGEABLE_ROLES = {
    "superadmin",
    "admin",
    "catalog_manager",
    "warehouse",
    "support",
    "marketing",
    "showroom_manager",
    "clienteling",
    "stylist",
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
