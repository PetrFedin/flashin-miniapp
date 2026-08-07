"""Add durable external-provider command outbox.

Revision ID: 0025_provider_command_outbox
Revises: 0024_inventory_movement_ledger
Create Date: 2026-08-07
"""

from alembic import op
import sqlalchemy as sa


revision = "0025_provider_command_outbox"
down_revision = "0024_inventory_movement_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_commands",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("command_type", sa.String(length=120), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("aggregate_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'sent', 'failed', 'review_required')",
            name="ck_provider_commands_status",
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name="ck_provider_commands_attempts_nonnegative",
        ),
        sa.UniqueConstraint(
            "provider",
            "idempotency_key",
            name="uq_provider_commands_provider_idempotency_key",
        ),
    )
    op.create_index("ix_provider_commands_provider", "provider_commands", ["provider"])
    op.create_index("ix_provider_commands_command_type", "provider_commands", ["command_type"])
    op.create_index("ix_provider_commands_aggregate_id", "provider_commands", ["aggregate_id"])
    op.create_index("ix_provider_commands_status", "provider_commands", ["status"])
    op.create_index("ix_provider_commands_next_attempt_at", "provider_commands", ["next_attempt_at"])
    op.create_index("ix_provider_commands_lease_token", "provider_commands", ["lease_token"])
    op.create_index("ix_provider_commands_created_at", "provider_commands", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_provider_commands_created_at", table_name="provider_commands")
    op.drop_index("ix_provider_commands_lease_token", table_name="provider_commands")
    op.drop_index("ix_provider_commands_next_attempt_at", table_name="provider_commands")
    op.drop_index("ix_provider_commands_status", table_name="provider_commands")
    op.drop_index("ix_provider_commands_aggregate_id", table_name="provider_commands")
    op.drop_index("ix_provider_commands_command_type", table_name="provider_commands")
    op.drop_index("ix_provider_commands_provider", table_name="provider_commands")
    op.drop_table("provider_commands")
