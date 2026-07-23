"""add showroom inventory reservation and order lifecycle

Revision ID: 0013_showroom_inventory_lifecycle
Revises: 0012_showroom_clienteling
Create Date: 2026-07-23
"""

from alembic import op
import sqlalchemy as sa


revision = "0013_showroom_inventory_lifecycle"
down_revision = "0012_showroom_clienteling"
branch_labels = None
depends_on = None


ROLE_PERMISSIONS = {
    "showroom_manager": (
        "products.read",
        "inventory.read",
        "orders.read",
        "customers.read",
        "crm.read",
        "crm.write",
        "notifications.read",
        "appointments.read",
        "appointments.write",
        "appointments.message",
        "appointments.manage_locations",
        "appointments.analytics",
    ),
    "clienteling": (
        "products.read",
        "inventory.read",
        "orders.read",
        "customers.read",
        "crm.read",
        "crm.write",
        "notifications.read",
        "appointments.read",
        "appointments.write",
        "appointments.message",
    ),
    "stylist": (
        "products.read",
        "inventory.read",
        "customers.read",
        "crm.read",
        "notifications.read",
        "appointments.read",
        "appointments.write",
        "appointments.message",
    ),
}


def _insert_missing_role_permissions() -> None:
    connection = op.get_bind()
    permissions = sa.table(
        "admin_role_permissions",
        sa.column("role", sa.String(length=64)),
        sa.column("permission", sa.String(length=120)),
    )
    existing = {
        (row.role, row.permission)
        for row in connection.execute(sa.select(permissions.c.role, permissions.c.permission))
    }
    additions = [
        {"role": role, "permission": permission}
        for role, role_permissions in ROLE_PERMISSIONS.items()
        for permission in role_permissions
        if (role, permission) not in existing
    ]
    if additions:
        connection.execute(sa.insert(permissions), additions)


def upgrade():
    op.add_column(
        "showroom_appointments",
        sa.Column("linked_order_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_showroom_appointments_linked_order_id_orders",
        "showroom_appointments",
        "orders",
        ["linked_order_id"],
        ["id"],
    )
    op.create_index(
        "ix_showroom_appointments_linked_order_id",
        "showroom_appointments",
        ["linked_order_id"],
    )

    op.add_column(
        "showroom_appointments",
        sa.Column("inventory_reserved", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "showroom_appointments",
        sa.Column("inventory_reserved_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "showroom_appointments",
        sa.Column("inventory_released_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "showroom_appointments",
        sa.Column("reservation_expires_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_showroom_appointments_inventory_reserved",
        "showroom_appointments",
        ["inventory_reserved"],
    )
    op.create_index(
        "ix_showroom_appointments_reservation_expires_at",
        "showroom_appointments",
        ["reservation_expires_at"],
    )
    op.create_index(
        "ix_showroom_appointments_reservation_expiry",
        "showroom_appointments",
        ["inventory_reserved", "reservation_expires_at"],
    )

    _insert_missing_role_permissions()


def downgrade():
    connection = op.get_bind()
    permissions = sa.table(
        "admin_role_permissions",
        sa.column("role", sa.String(length=64)),
        sa.column("permission", sa.String(length=120)),
    )
    for role, role_permissions in ROLE_PERMISSIONS.items():
        connection.execute(
            sa.delete(permissions).where(
                permissions.c.role == role,
                permissions.c.permission.in_(role_permissions),
            )
        )

    op.drop_index("ix_showroom_appointments_reservation_expiry", table_name="showroom_appointments")
    op.drop_index("ix_showroom_appointments_reservation_expires_at", table_name="showroom_appointments")
    op.drop_index("ix_showroom_appointments_inventory_reserved", table_name="showroom_appointments")
    op.drop_column("showroom_appointments", "reservation_expires_at")
    op.drop_column("showroom_appointments", "inventory_released_at")
    op.drop_column("showroom_appointments", "inventory_reserved_at")
    op.drop_column("showroom_appointments", "inventory_reserved")

    op.drop_index("ix_showroom_appointments_linked_order_id", table_name="showroom_appointments")
    op.drop_constraint(
        "fk_showroom_appointments_linked_order_id_orders",
        "showroom_appointments",
        type_="foreignkey",
    )
    op.drop_column("showroom_appointments", "linked_order_id")
