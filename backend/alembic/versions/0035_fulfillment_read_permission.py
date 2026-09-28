"""Split fulfillment reads from generic order access.

Revision ID: 0035_fulfillment_read_permission
Revises: 0034_notification_policy_context
Create Date: 2026-08-28
"""

from alembic import op
import sqlalchemy as sa


revision = "0035_fulfillment_read_permission"
down_revision = "0034_notification_policy_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Preserve access only for configured roles that could read fulfillment before."""

    op.execute(
        sa.text(
            """
            INSERT INTO admin_role_permissions (role, permission)
            SELECT source.role, 'fulfillment.read'
            FROM (
                SELECT DISTINCT role
                FROM admin_role_permissions
                WHERE role IN ('manager', 'warehouse')
                  AND permission = 'orders.read'
            ) AS source
            WHERE NOT EXISTS (
                SELECT 1
                FROM admin_role_permissions AS existing
                WHERE existing.role = source.role
                  AND existing.permission = 'fulfillment.read'
            )
            """
        )
    )


def downgrade() -> None:
    # This is intentionally a data-safe no-op. A matching fulfillment.read row
    # may have existed before this migration, so deleting it would risk
    # destroying an administrator's explicit RBAC configuration. Older code
    # ignores this permission and continues to authorize reads via orders.read.
    pass
