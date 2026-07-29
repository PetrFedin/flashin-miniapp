"""add durable payment creation attempts

Revision ID: 0017_payment_creation_attempts
Revises: 0016_admin_totp_replay_state
Create Date: 2026-07-29
"""

from alembic import op
import sqlalchemy as sa

revision = "0017_payment_creation_attempts"
down_revision = "0016_admin_totp_replay_state"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "payment_creation_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False, server_default="yookassa"),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=64), nullable=False, server_default="creating"),
        sa.Column("provider_payment_id", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name="fk_payment_creation_attempts_order",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_payment_creation_attempts"),
        sa.UniqueConstraint(
            "order_id",
            "provider",
            "attempt_number",
            name="uq_payment_creation_attempt_order_provider_number",
        ),
    )
    op.create_index(
        "ix_payment_creation_attempts_order_id",
        "payment_creation_attempts",
        ["order_id"],
        unique=False,
    )
    op.create_index(
        "ix_payment_creation_attempts_status",
        "payment_creation_attempts",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_payment_creation_attempts_provider_payment_id",
        "payment_creation_attempts",
        ["provider_payment_id"],
        unique=False,
    )
    op.create_index(
        "ix_payment_creation_attempts_lease_expires_at",
        "payment_creation_attempts",
        ["lease_expires_at"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_payment_creation_attempts_lease_expires_at", table_name="payment_creation_attempts")
    op.drop_index("ix_payment_creation_attempts_provider_payment_id", table_name="payment_creation_attempts")
    op.drop_index("ix_payment_creation_attempts_status", table_name="payment_creation_attempts")
    op.drop_index("ix_payment_creation_attempts_order_id", table_name="payment_creation_attempts")
    op.drop_table("payment_creation_attempts")
