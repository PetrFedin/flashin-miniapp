"""support privacy outbox

Revision ID: 0002_support_privacy_outbox
Revises: 0001_initial_production
Create Date: 2026-06-01
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_support_privacy_outbox"
down_revision = "0001_initial_production"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "admin_role_permissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("permission", sa.String(120), nullable=False),
    )
    op.create_index("ix_admin_role_permissions_role", "admin_role_permissions", ["role"])
    op.create_index("ix_admin_role_permissions_permission", "admin_role_permissions", ["permission"])

    op.create_table(
        "support_tickets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=True),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(64), nullable=False, server_default="open"),
        sa.Column("priority", sa.String(32), nullable=False, server_default="normal"),
        sa.Column("assigned_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "webhook_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("destination", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("status", sa.String(64), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_webhook_outbox_event_type", "webhook_outbox", ["event_type"])

    op.create_table(
        "privacy_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("request_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(64), nullable=False, server_default="requested"),
        sa.Column("result_url", sa.String(2048), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_privacy_requests_customer_id", "privacy_requests", ["customer_id"])

    op.create_table(
        "consent_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("consent_type", sa.String(120), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("source", sa.String(120), nullable=False, server_default="telegram_mini_app"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_consent_records_customer_id", "consent_records", ["customer_id"])


def downgrade():
    op.drop_table("consent_records")
    op.drop_table("privacy_requests")
    op.drop_table("webhook_outbox")
    op.drop_table("support_tickets")
    op.drop_table("admin_role_permissions")
