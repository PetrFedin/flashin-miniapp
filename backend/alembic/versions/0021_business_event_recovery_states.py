"""add durable business event recovery diagnostics

Revision ID: 0021_business_event_recovery_states
Revises: 0020_notification_delivery_lease_tokens
Create Date: 2026-08-03
"""

from alembic import op
import sqlalchemy as sa

revision = "0021_business_event_recovery_states"
down_revision = "0020_notification_delivery_lease_tokens"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "business_event_recovery_states",
        sa.Column("business_event_id", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), server_default="", nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("failed_at", sa.DateTime(), nullable=True),
        sa.Column("replay_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_replayed_at", sa.DateTime(), nullable=True),
        sa.Column("last_replayed_by_admin_id", sa.Integer(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["business_event_id"],
            ["business_events.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["last_replayed_by_admin_id"],
            ["admin_users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("business_event_id"),
    )
    op.create_index(
        "ix_business_event_recovery_states_last_attempt_at",
        "business_event_recovery_states",
        ["last_attempt_at"],
    )
    op.create_index(
        "ix_business_event_recovery_states_failed_at",
        "business_event_recovery_states",
        ["failed_at"],
    )


def downgrade():
    op.drop_index(
        "ix_business_event_recovery_states_failed_at",
        table_name="business_event_recovery_states",
    )
    op.drop_index(
        "ix_business_event_recovery_states_last_attempt_at",
        table_name="business_event_recovery_states",
    )
    op.drop_table("business_event_recovery_states")
