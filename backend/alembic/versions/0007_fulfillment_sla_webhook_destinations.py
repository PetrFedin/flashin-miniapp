"""fulfillment sla webhook destinations

Revision ID: 0007_fulfillment_sla_webhook_destinations
Revises: 0006_reconciliation_campaign_timeline_loyalty_holds
Create Date: 2026-06-01
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_fulfillment_sla_webhook_destinations"
down_revision = "0006_reconciliation_campaign_timeline_loyalty_holds"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "fulfillment_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("status", sa.String(64), nullable=False, server_default="new"),
        sa.Column("assigned_admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("pick_started_at", sa.DateTime(), nullable=True),
        sa.Column("packed_at", sa.DateTime(), nullable=True),
        sa.Column("ready_at", sa.DateTime(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_fulfillment_tasks_order_id", "fulfillment_tasks", ["order_id"])

    op.create_table(
        "fulfillment_task_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("fulfillment_tasks.id"), nullable=False),
        sa.Column("order_item_id", sa.Integer(), sa.ForeignKey("order_items.id"), nullable=False),
        sa.Column("status", sa.String(64), nullable=False, server_default="to_pick"),
        sa.Column("picked_qty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("issue", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_fulfillment_task_items_task_id", "fulfillment_task_items", ["task_id"])
    op.create_index("ix_fulfillment_task_items_order_item_id", "fulfillment_task_items", ["order_item_id"])

    op.create_table(
        "sla_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("due_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(64), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_sla_events_order_id", "sla_events", ["order_id"])
    op.create_index("ix_sla_events_event_type", "sla_events", ["event_type"])

    op.create_table(
        "webhook_destinations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("event_type", sa.String(120), nullable=False, server_default="*"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("signing_secret", sa.String(255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("webhook_destinations")
    op.drop_table("sla_events")
    op.drop_table("fulfillment_task_items")
    op.drop_table("fulfillment_tasks")
