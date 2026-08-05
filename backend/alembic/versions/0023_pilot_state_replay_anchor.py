"""add replay-resistant pilot state anchor

Revision ID: 0023_pilot_state_replay_anchor
Revises: 0022_pilot_runtime_guard
Create Date: 2026-08-05
"""

from alembic import op
import sqlalchemy as sa

revision = "0023_pilot_state_replay_anchor"
down_revision = "0022_pilot_runtime_guard"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "pilot_runtime_state",
        sa.Column("pilot_state_revision", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "pilot_runtime_state",
        sa.Column("pilot_state_sha256", sa.String(length=64), server_default="", nullable=False),
    )
    op.create_check_constraint(
        "ck_pilot_runtime_state_revision",
        "pilot_runtime_state",
        "pilot_state_revision >= 0",
    )
    op.create_check_constraint(
        "ck_pilot_runtime_state_anchor",
        "pilot_runtime_state",
        "(pilot_state_revision = 0 AND pilot_state_sha256 = '') OR "
        "(pilot_state_revision >= 1 AND length(pilot_state_sha256) = 64)",
    )


def downgrade():
    op.drop_constraint(
        "ck_pilot_runtime_state_anchor", "pilot_runtime_state", type_="check"
    )
    op.drop_constraint(
        "ck_pilot_runtime_state_revision", "pilot_runtime_state", type_="check"
    )
    op.drop_column("pilot_runtime_state", "pilot_state_sha256")
    op.drop_column("pilot_runtime_state", "pilot_state_revision")
