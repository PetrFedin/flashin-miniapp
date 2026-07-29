"""notification delivery retry state

Revision ID: 0012_notification_delivery_retry_state
Revises: 0011_refund_integrity_and_loyalty_reversals
Create Date: 2026-07-29
"""

from alembic import op
import sqlalchemy as sa

revision = "0012_notification_delivery_retry_state"
down_revision = "0011_refund_integrity_and_loyalty_reversals"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "notification_delivery_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "notification_id",
            sa.Integer(),
            sa.ForeignKey("notifications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint(
            "notification_id",
            name="uq_notification_delivery_state_notification",
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name="ck_notification_delivery_state_attempts_nonnegative",
        ),
    )
    op.create_index(
        "ix_notification_delivery_states_notification_id",
        "notification_delivery_states",
        ["notification_id"],
    )
    op.create_index(
        "ix_notification_delivery_states_next_attempt_at",
        "notification_delivery_states",
        ["next_attempt_at"],
    )


def downgrade():
    op.drop_index(
        "ix_notification_delivery_states_next_attempt_at",
        table_name="notification_delivery_states",
    )
    op.drop_index(
        "ix_notification_delivery_states_notification_id",
        table_name="notification_delivery_states",
    )
    op.drop_table("notification_delivery_states")
