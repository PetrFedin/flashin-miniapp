"""add webhook outbox lease ownership

Revision ID: 0019_webhook_outbox_lease_tokens
Revises: 0018_notification_event_keys
Create Date: 2026-08-03
"""

from alembic import op
import sqlalchemy as sa

revision = "0019_webhook_outbox_lease_tokens"
down_revision = "0018_notification_event_keys"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "webhook_outbox",
        sa.Column("lease_token", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_webhook_outbox_lease_token",
        "webhook_outbox",
        ["lease_token"],
    )


def downgrade():
    op.drop_index(
        "ix_webhook_outbox_lease_token",
        table_name="webhook_outbox",
    )
    op.drop_column("webhook_outbox", "lease_token")
