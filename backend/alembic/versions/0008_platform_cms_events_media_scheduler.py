"""platform cms events media scheduler

Revision ID: 0008_platform_cms_events_media_scheduler
Revises: 0007_fulfillment_sla_webhook_destinations
Create Date: 2026-06-01
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_platform_cms_events_media_scheduler"
down_revision = "0007_fulfillment_sla_webhook_destinations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "feature_flags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(120), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_feature_flags_key", "feature_flags", ["key"], unique=True)

    op.create_table(
        "remote_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(120), nullable=False),
        sa.Column("value_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_remote_configs_key", "remote_configs", ["key"], unique=True)

    op.create_table(
        "cms_pages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("content_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_cms_pages_slug", "cms_pages", ["slug"], unique=True)

    op.create_table(
        "cms_blocks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("page_slug", sa.String(255), nullable=False),
        sa.Column("block_type", sa.String(120), nullable=False),
        sa.Column("title", sa.String(255), nullable=False, server_default=""),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.create_index("ix_cms_blocks_page_slug", "cms_blocks", ["page_slug"])

    op.create_table(
        "business_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("aggregate_type", sa.String(120), nullable=False, server_default=""),
        sa.Column("aggregate_id", sa.String(120), nullable=False, server_default=""),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(64), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_business_events_event_type", "business_events", ["event_type"])

    op.create_table(
        "audit_trails",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("action", sa.String(120), nullable=False),
        sa.Column("entity_type", sa.String(120), nullable=False, server_default=""),
        sa.Column("entity_id", sa.String(120), nullable=False, server_default=""),
        sa.Column("before_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("after_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("ip_address", sa.String(120), nullable=False, server_default=""),
        sa.Column("user_agent", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_audit_trails_action", "audit_trails", ["action"])

    op.create_table(
        "media_derivatives",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("media_asset_id", sa.Integer(), sa.ForeignKey("media_assets.id"), nullable=False),
        sa.Column("derivative_type", sa.String(120), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False, server_default=""),
        sa.Column("width", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("height", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_type", sa.String(120), nullable=False, server_default=""),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_media_derivatives_media_asset_id", "media_derivatives", ["media_asset_id"])

    op.create_table(
        "scheduled_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_key", sa.String(120), nullable=False),
        sa.Column("cron", sa.String(120), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_status", sa.String(64), nullable=False, server_default=""),
    )
    op.create_index("ix_scheduled_jobs_job_key", "scheduled_jobs", ["job_key"], unique=True)


def downgrade():
    op.drop_table("scheduled_jobs")
    op.drop_table("media_derivatives")
    op.drop_table("audit_trails")
    op.drop_table("business_events")
    op.drop_table("cms_blocks")
    op.drop_table("cms_pages")
    op.drop_table("remote_configs")
    op.drop_table("feature_flags")
