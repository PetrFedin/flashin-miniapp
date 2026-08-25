from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import AdminRolePermission, AdminUser


REFUNDS_WRITE_PERMISSION = "refunds.write"
DELIVERY_PROVIDERS_WRITE_PERMISSION = "delivery.providers.write"
PAYMENT_RECONCILIATION_READ_PERMISSION = "payments.reconciliation.read"
PAYMENT_RECONCILIATION_WRITE_PERMISSION = "payments.reconciliation.write"
WEBHOOKS_CONFIGURE_PERMISSION = "webhooks.configure"

DEFAULT_PERMISSIONS = {
    "owner": {"*"},
    "manager": {
        "products.read",
        "products.write",
        "orders.read",
        "orders.write",
        "fulfillment.write",
        "promo.write",
        "support.write",
        "showroom.read",
        "showroom.write",
        "notifications.read",
        "notifications.retry",
        "webhooks.read",
        "webhooks.write",
        "media.write",
        "security.read",
        "privacy.read",
    },
    "support": {
        "orders.read",
        "support.write",
        "customers.read",
        "showroom.read",
        "showroom.write",
        "notifications.read",
        "notifications.retry",
        "webhooks.read",
    },
    "warehouse": {
        "products.read",
        "inventory.write",
        "orders.read",
        "fulfillment.write",
        "media.write",
    },
}


def effective_permissions(db: Session, admin: AdminUser) -> set[str]:
    """Return the exact permission set used by authorization decisions.

    A role with any database-configured rows uses those rows as its complete
    permission set, matching the historical authorization semantics. Owners
    remain unrestricted and are represented by the explicit wildcard.
    """

    if admin.role == "owner":
        return {"*"}
    configured = db.query(AdminRolePermission).filter(AdminRolePermission.role == admin.role).all()
    if configured:
        return {str(row.permission).strip() for row in configured if str(row.permission).strip()}
    return set(DEFAULT_PERMISSIONS.get(admin.role, set()))


def has_permission(db: Session, admin: AdminUser, permission: str) -> bool:
    permissions = effective_permissions(db, admin)
    return "*" in permissions or permission in permissions


def require_permission(db: Session, admin: AdminUser, permission: str) -> None:
    if not has_permission(db, admin, permission):
        raise HTTPException(status_code=403, detail=f"Missing permission: {permission}")
