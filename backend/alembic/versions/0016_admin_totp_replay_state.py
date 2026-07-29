"""add admin TOTP replay state

Revision ID: 0016_admin_totp_replay_state
Revises: 0015_checkout_idempotency
Create Date: 2026-07-29
"""

from alembic import op
import sqlalchemy as sa

revision = "0016_admin_totp_replay_state"
down_revision = "0015_checkout_idempotency"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "admin_totp_replay_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("admin_id", sa.Integer(), nullable=False),
        sa.Column("last_used_counter", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["admin_id"],
            ["admin_users.id"],
            name="fk_admin_totp_replay_state_admin",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_admin_totp_replay_state"),
        sa.UniqueConstraint(
            "admin_id",
            name="uq_admin_totp_replay_state_admin_id",
        ),
    )
    op.create_index(
        "ix_admin_totp_replay_state_admin_id",
        "admin_totp_replay_state",
        ["admin_id"],
        unique=True,
    )


def downgrade():
    op.drop_index(
        "ix_admin_totp_replay_state_admin_id",
        table_name="admin_totp_replay_state",
    )
    op.drop_table("admin_totp_replay_state")
