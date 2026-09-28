"""Bind notification delivery policy to purpose and customer context.

Revision ID: 0034_notification_policy_context
Revises: 0033_admin_totp_replay_state
Create Date: 2026-08-26
"""

from alembic import op
import sqlalchemy as sa


revision = "0034_notification_policy_context"
down_revision = "0033_admin_totp_replay_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notification_policy_contexts",
        sa.Column("notification_id", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("campaign_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "purpose IN ('transactional', 'marketing')",
            name="ck_notification_policy_context_purpose",
        ),
        sa.ForeignKeyConstraint(
            ["notification_id"],
            ["notifications.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["marketing_campaigns.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("notification_id"),
    )
    op.create_index(
        "ix_notification_policy_contexts_purpose",
        "notification_policy_contexts",
        ["purpose"],
    )
    op.create_index(
        "ix_notification_policy_contexts_customer_id",
        "notification_policy_contexts",
        ["customer_id"],
    )
    op.create_index(
        "ix_notification_policy_contexts_campaign_id",
        "notification_policy_contexts",
        ["campaign_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notification_policy_contexts_campaign_id",
        table_name="notification_policy_contexts",
    )
    op.drop_index(
        "ix_notification_policy_contexts_customer_id",
        table_name="notification_policy_contexts",
    )
    op.drop_index(
        "ix_notification_policy_contexts_purpose",
        table_name="notification_policy_contexts",
    )
    op.drop_table("notification_policy_contexts")
