"""add showroom clienteling and appointment workflow

Revision ID: 0012_showroom_clienteling
Revises: 0011_migrate_granular_rbac_permissions
Create Date: 2026-07-22
"""

from alembic import op
import sqlalchemy as sa


revision = "0012_showroom_clienteling"
down_revision = "0011_migrate_granular_rbac_permissions"
branch_labels = None
depends_on = None


ROLE_PERMISSIONS = {
    "admin": (
        "appointments.read",
        "appointments.write",
        "appointments.message",
        "appointments.manage_locations",
        "appointments.analytics",
    ),
    "manager": (
        "appointments.read",
        "appointments.write",
        "appointments.message",
        "appointments.manage_locations",
        "appointments.analytics",
    ),
    "support": (
        "appointments.read",
        "appointments.write",
        "appointments.message",
    ),
    "showroom_manager": (
        "appointments.read",
        "appointments.write",
        "appointments.message",
        "customers.read",
        "crm.read",
        "notifications.read",
    ),
}


def upgrade():
    op.create_table(
        "showroom_locations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("address", sa.Text(), nullable=False, server_default=""),
        sa.Column("city", sa.String(length=120), nullable=False, server_default="Москва"),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Europe/Moscow"),
        sa.Column("phone", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("slot_duration_minutes", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("opening_hours_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_showroom_locations_active", "showroom_locations", ["active"])

    op.create_table(
        "product_showroom_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False),
        sa.Column("availability_status", sa.String(length=32), nullable=False, server_default="in_stock"),
        sa.Column("preorder_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("fitting_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("expected_at", sa.DateTime(), nullable=True),
        sa.Column("showroom_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("product_id", name="uq_product_showroom_profile"),
    )
    op.create_index("ix_product_showroom_profiles_product_id", "product_showroom_profiles", ["product_id"])
    op.create_index("ix_product_showroom_profiles_availability", "product_showroom_profiles", ["availability_status"])

    op.create_table(
        "showroom_appointments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("variant_id", sa.Integer(), sa.ForeignKey("product_variants.id"), nullable=True),
        sa.Column("showroom_id", sa.Integer(), sa.ForeignKey("showroom_locations.id"), nullable=True),
        sa.Column("assigned_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("request_type", sa.String(length=32), nullable=False, server_default="fitting"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="requested"),
        sa.Column("preferred_start", sa.DateTime(), nullable=False),
        sa.Column("alternative_start", sa.DateTime(), nullable=True),
        sa.Column("proposed_start", sa.DateTime(), nullable=True),
        sa.Column("confirmed_start", sa.DateTime(), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("size", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("color", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("contact_phone", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("customer_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("manager_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=64), nullable=False, server_default="telegram_mini_app"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_showroom_appointments_customer_id", "showroom_appointments", ["customer_id"])
    op.create_index("ix_showroom_appointments_product_id", "showroom_appointments", ["product_id"])
    op.create_index("ix_showroom_appointments_variant_id", "showroom_appointments", ["variant_id"])
    op.create_index("ix_showroom_appointments_showroom_id", "showroom_appointments", ["showroom_id"])
    op.create_index("ix_showroom_appointments_assigned_admin_id", "showroom_appointments", ["assigned_admin_id"])
    op.create_index("ix_showroom_appointments_status", "showroom_appointments", ["status"])
    op.create_index("ix_showroom_appointments_request_type", "showroom_appointments", ["request_type"])
    op.create_index("ix_showroom_appointments_preferred_start", "showroom_appointments", ["preferred_start"])
    op.create_index("ix_showroom_appointments_confirmed_start", "showroom_appointments", ["confirmed_start"])
    op.create_index(
        "ix_showroom_appointments_queue",
        "showroom_appointments",
        ["status", "showroom_id", "confirmed_start"],
    )

    op.create_table(
        "showroom_appointment_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "appointment_id",
            sa.Integer(),
            sa.ForeignKey("showroom_appointments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sender_type", sa.String(length=24), nullable=False),
        sa.Column("sender_customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("sender_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_showroom_appointment_messages_appointment_id",
        "showroom_appointment_messages",
        ["appointment_id"],
    )
    op.create_index("ix_showroom_appointment_messages_sender_type", "showroom_appointment_messages", ["sender_type"])
    op.create_index("ix_showroom_appointment_messages_created_at", "showroom_appointment_messages", ["created_at"])

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

    op.drop_index("ix_showroom_appointment_messages_created_at", table_name="showroom_appointment_messages")
    op.drop_index("ix_showroom_appointment_messages_sender_type", table_name="showroom_appointment_messages")
    op.drop_index("ix_showroom_appointment_messages_appointment_id", table_name="showroom_appointment_messages")
    op.drop_table("showroom_appointment_messages")

    op.drop_index("ix_showroom_appointments_queue", table_name="showroom_appointments")
    op.drop_index("ix_showroom_appointments_confirmed_start", table_name="showroom_appointments")
    op.drop_index("ix_showroom_appointments_preferred_start", table_name="showroom_appointments")
    op.drop_index("ix_showroom_appointments_request_type", table_name="showroom_appointments")
    op.drop_index("ix_showroom_appointments_status", table_name="showroom_appointments")
    op.drop_index("ix_showroom_appointments_assigned_admin_id", table_name="showroom_appointments")
    op.drop_index("ix_showroom_appointments_showroom_id", table_name="showroom_appointments")
    op.drop_index("ix_showroom_appointments_variant_id", table_name="showroom_appointments")
    op.drop_index("ix_showroom_appointments_product_id", table_name="showroom_appointments")
    op.drop_index("ix_showroom_appointments_customer_id", table_name="showroom_appointments")
    op.drop_table("showroom_appointments")

    op.drop_index("ix_product_showroom_profiles_availability", table_name="product_showroom_profiles")
    op.drop_index("ix_product_showroom_profiles_product_id", table_name="product_showroom_profiles")
    op.drop_table("product_showroom_profiles")

    op.drop_index("ix_showroom_locations_active", table_name="showroom_locations")
    op.drop_table("showroom_locations")
