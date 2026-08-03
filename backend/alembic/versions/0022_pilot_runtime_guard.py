"""add fail-closed pilot runtime guard

Revision ID: 0022_pilot_runtime_guard
Revises: 0021_business_event_recovery_states
Create Date: 2026-08-03
"""

from alembic import op
import sqlalchemy as sa

revision = "0022_pilot_runtime_guard"
down_revision = "0021_business_event_recovery_states"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "pilot_runtime_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=64), server_default="", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="closed", nullable=False),
        sa.Column("admission_sha256", sa.String(length=64), server_default="", nullable=False),
        sa.Column("release_sha256", sa.String(length=64), server_default="", nullable=False),
        sa.Column("pilot_state_created_at", sa.String(length=64), server_default="", nullable=False),
        sa.Column("max_orders", sa.Integer(), server_default="20", nullable=False),
        sa.Column("accepted_orders", sa.Integer(), server_default="0", nullable=False),
        sa.Column("allowed_telegram_ids", sa.Text(), server_default="[]", nullable=False),
        sa.Column("opened_at", sa.DateTime(), nullable=True),
        sa.Column("stopped_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("stop_reason", sa.Text(), server_default="", nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('closed', 'active', 'stopped', 'completed')",
            name="ck_pilot_runtime_state_status",
        ),
        sa.CheckConstraint(
            "max_orders BETWEEN 1 AND 20",
            name="ck_pilot_runtime_state_max_orders",
        ),
        sa.CheckConstraint(
            "accepted_orders >= 0 AND accepted_orders <= max_orders",
            name="ck_pilot_runtime_state_accepted_orders",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_pilot_runtime_state_status",
        "pilot_runtime_state",
        ["status"],
    )
    op.execute(
        "INSERT INTO pilot_runtime_state "
        "(id, run_id, status, admission_sha256, release_sha256, pilot_state_created_at, "
        "max_orders, accepted_orders, allowed_telegram_ids, stop_reason) "
        "VALUES (1, '', 'closed', '', '', '', 20, 0, '[]', '')"
    )

    op.create_table(
        "pilot_order_slots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("admission_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "sequence BETWEEN 1 AND 20",
            name="ck_pilot_order_slot_sequence",
        ),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_pilot_order_slot_run_sequence"),
    )
    op.create_index("ix_pilot_order_slots_customer_id", "pilot_order_slots", ["customer_id"])
    op.create_index("ix_pilot_order_slots_order_id", "pilot_order_slots", ["order_id"])
    op.create_index("ix_pilot_order_slots_run_id", "pilot_order_slots", ["run_id"])


def downgrade():
    op.drop_index("ix_pilot_order_slots_run_id", table_name="pilot_order_slots")
    op.drop_index("ix_pilot_order_slots_order_id", table_name="pilot_order_slots")
    op.drop_index("ix_pilot_order_slots_customer_id", table_name="pilot_order_slots")
    op.drop_table("pilot_order_slots")
    op.drop_index("ix_pilot_runtime_state_status", table_name="pilot_runtime_state")
    op.drop_table("pilot_runtime_state")
