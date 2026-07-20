"""copy legacy RBAC grants to granular permissions

Revision ID: 0011_migrate_granular_rbac_permissions
Revises: 0010_enterprise_telegram_commerce
Create Date: 2026-07-20
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_migrate_granular_rbac_permissions"
down_revision = "0010_enterprise_telegram_commerce"
branch_labels = None
depends_on = None


PERMISSION_MIGRATIONS = {
    "orders.read": (
        "admin_security.read",
        "analytics.read",
        "audit.read",
        "delivery.read",
        "diagnostics.read",
        "events.read",
        "fulfillment.read",
        "payments.reconcile.read",
        "webhooks.read",
    ),
    "orders.write": (
        "admin_security.write",
        "delivery.write",
        "feature_flags.write",
        "fulfillment.write",
        "payments.reconcile.write",
        "remote_config.write",
        "webhooks.write",
    ),
    "support.write": (
        "campaigns.read",
        "campaigns.write",
        "support.read",
    ),
    "customers.read": (
        "crm.read",
        "crm.write",
        "privacy.read",
        "privacy.write",
    ),
    "products.read": ("moysklad.read",),
    "products.write": (
        "cms.write",
        "moysklad.sync",
        "moysklad.write",
    ),
    "inventory.write": ("inventory.read",),
}


admin_role_permissions = sa.table(
    "admin_role_permissions",
    sa.column("id", sa.Integer()),
    sa.column("role", sa.String(64)),
    sa.column("permission", sa.String(120)),
)


def upgrade():
    connection = op.get_bind()
    configured = {
        (row.role, row.permission)
        for row in connection.execute(
            sa.select(
                admin_role_permissions.c.role,
                admin_role_permissions.c.permission,
            )
        )
    }
    additions = []
    for legacy_permission, granular_permissions in PERMISSION_MIGRATIONS.items():
        roles = {
            role
            for role, permission in configured
            if permission == legacy_permission
        }
        for role in roles:
            for granular_permission in granular_permissions:
                pair = (role, granular_permission)
                if pair not in configured:
                    additions.append(
                        {"role": role, "permission": granular_permission}
                    )
                    configured.add(pair)
    if additions:
        connection.execute(sa.insert(admin_role_permissions), additions)


def downgrade():
    """Safe no-op: added grants cannot be distinguished from user-created grants."""
