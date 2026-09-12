"""Persist the last consumed administrator TOTP counter.

Revision ID: 0033_admin_totp_replay_state
Revises: 0032_fixed_precision_money
Create Date: 2026-08-21
"""

from alembic import op
import sqlalchemy as sa


revision = "0033_admin_totp_replay_state"
down_revision = "0032_fixed_precision_money"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_totp_replay_states",
        sa.Column("admin_id", sa.Integer(), nullable=False),
        sa.Column("last_used_counter", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["admin_id"],
            ["admin_users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("admin_id"),
    )


def downgrade() -> None:
    op.drop_table("admin_totp_replay_states")
