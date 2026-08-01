"""add durable notification event keys

Revision ID: 0018_notification_event_keys
Revises: 0017_promo_definition_constraints
Create Date: 2026-08-02
"""

from alembic import op
import sqlalchemy as sa

revision = "0018_notification_event_keys"
down_revision = "0017_promo_definition_constraints"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "notification_event_keys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_key", sa.String(length=255), nullable=False),
        sa.Column(
            "notification_id",
            sa.Integer(),
            sa.ForeignKey("notifications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP AT TIME ZONE 'UTC'"),
        ),
        sa.UniqueConstraint(
            "event_key",
            name="uq_notification_event_keys_event_key",
        ),
        sa.UniqueConstraint(
            "notification_id",
            name="uq_notification_event_keys_notification",
        ),
    )
    op.create_index(
        "ix_notification_event_keys_event_key",
        "notification_event_keys",
        ["event_key"],
    )
    op.create_index(
        "ix_notification_event_keys_notification_id",
        "notification_event_keys",
        ["notification_id"],
    )


def downgrade():
    op.drop_index(
        "ix_notification_event_keys_notification_id",
        table_name="notification_event_keys",
    )
    op.drop_index(
        "ix_notification_event_keys_event_key",
        table_name="notification_event_keys",
    )
    op.drop_table("notification_event_keys")
