"""Add durable logical worker heartbeats for pilot admission.

Revision ID: 0027_pilot_worker_heartbeats
Revises: 0026_inventory_return_movement
Create Date: 2026-08-09
"""

from alembic import op
import sqlalchemy as sa


revision = "0027_pilot_worker_heartbeats"
down_revision = "0026_inventory_return_movement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pilot_worker_heartbeats",
        sa.Column("worker_name", sa.String(length=32), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "worker_name IN ('scheduler', 'notification_worker')",
            name="ck_pilot_worker_heartbeat_name",
        ),
        sa.PrimaryKeyConstraint("worker_name"),
    )


def downgrade() -> None:
    op.drop_table("pilot_worker_heartbeats")
