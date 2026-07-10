"""security payment delivery media hardening

Revision ID: 0009_security_payment_delivery_media_hardening
Revises: 0008_platform_cms_events_media_scheduler
Create Date: 2026-06-01
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_security_payment_delivery_media_hardening"
down_revision = "0008_platform_cms_events_media_scheduler"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("admin_login_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("email", sa.String(255), nullable=False, server_default=""),
        sa.Column("ip_address", sa.String(120), nullable=False, server_default=""),
        sa.Column("user_agent", sa.Text(), nullable=False, server_default=""),
        sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("reason", sa.String(255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table("admin_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=False),
        sa.Column("session_token_hash", sa.String(255), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("ip_address", sa.String(120), nullable=False, server_default=""),
        sa.Column("user_agent", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_admin_sessions_admin_id", "admin_sessions", ["admin_id"])
    op.create_index("ix_admin_sessions_session_token_hash", "admin_sessions", ["session_token_hash"])

    op.create_table("admin_password_resets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=False),
        sa.Column("token_hash", sa.String(255), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_admin_password_resets_admin_id", "admin_password_resets", ["admin_id"])
    op.create_index("ix_admin_password_resets_token_hash", "admin_password_resets", ["token_hash"])

    op.create_table("admin_totp_secrets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=False),
        sa.Column("secret", sa.String(255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_admin_totp_secrets_admin_id", "admin_totp_secrets", ["admin_id"], unique=True)

    op.create_table("admin_ip_allowlist",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cidr", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_unique_constraint("uq_admin_ip_allowlist_cidr", "admin_ip_allowlist", ["cidr"])

    op.create_table("payment_reconciliations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("payment_id", sa.Integer(), sa.ForeignKey("payments.id"), nullable=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=True),
        sa.Column("provider_payment_id", sa.String(255), nullable=False, server_default=""),
        sa.Column("local_status", sa.String(64), nullable=False, server_default=""),
        sa.Column("provider_status", sa.String(64), nullable=False, server_default=""),
        sa.Column("amount_local", sa.Float(), nullable=False, server_default="0"),
        sa.Column("amount_provider", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(64), nullable=False, server_default="open"),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_payment_reconciliations_provider_payment_id", "payment_reconciliations", ["provider_payment_id"])

    op.create_table("delivery_providers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("config_json", sa.Text(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_delivery_providers_code", "delivery_providers", ["code"], unique=True)

    op.create_table("delivery_shipments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("provider_code", sa.String(64), nullable=False, server_default="courier"),
        sa.Column("tracking_number", sa.String(255), nullable=False, server_default=""),
        sa.Column("status", sa.String(64), nullable=False, server_default="created"),
        sa.Column("price", sa.Float(), nullable=False, server_default="0"),
        sa.Column("raw_payload", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_delivery_shipments_order_id", "delivery_shipments", ["order_id"])

    op.create_table("moysklad_sku_matches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("local_variant_id", sa.Integer(), sa.ForeignKey("product_variants.id"), nullable=False),
        sa.Column("moysklad_id", sa.String(255), nullable=False),
        sa.Column("external_sku", sa.String(120), nullable=False, server_default=""),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_moysklad_sku_matches_local_variant_id", "moysklad_sku_matches", ["local_variant_id"])
    op.create_index("ix_moysklad_sku_matches_moysklad_id", "moysklad_sku_matches", ["moysklad_id"])

    op.create_table("media_processing_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("media_asset_id", sa.Integer(), sa.ForeignKey("media_assets.id"), nullable=False),
        sa.Column("status", sa.String(64), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_media_processing_jobs_media_asset_id", "media_processing_jobs", ["media_asset_id"])


def downgrade():
    op.drop_table("media_processing_jobs")
    op.drop_table("moysklad_sku_matches")
    op.drop_table("delivery_shipments")
    op.drop_table("delivery_providers")
    op.drop_table("payment_reconciliations")
    op.drop_table("admin_ip_allowlist")
    op.drop_table("admin_totp_secrets")
    op.drop_table("admin_password_resets")
    op.drop_table("admin_sessions")
    op.drop_table("admin_login_events")
