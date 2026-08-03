"""add notification delivery lease ownership

Revision ID: 0020_notification_delivery_lease_tokens
Revises: 0019_webhook_outbox_lease_tokens
Create Date: 2026-08-03
"""

from alembic import op
import sqlalchemy as sa

revision = "0020_notification_delivery_lease_tokens"
down_revision = "0019_webhook_outbox_lease_tokens"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "notification_delivery_states",
        sa.Column("lease_token", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_notification_delivery_states_lease_token",
        "notification_delivery_states",
        ["lease_token"],
    )


def downgrade():
    op.drop_index(
        "ix_notification_delivery_states_lease_token",
        table_name="notification_delivery_states",
    )
    op.drop_column("notification_delivery_states", "lease_token")
