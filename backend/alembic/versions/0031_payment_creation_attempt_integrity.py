"""Add durable payment creation attempt integrity.

Revision ID: 0031_payment_creation_attempt_integrity
Revises: 0030_merchandising_promo_price
Create Date: 2026-08-19
"""

from alembic import op
import sqlalchemy as sa


revision = "0031_payment_creation_attempt_integrity"
down_revision = "0030_merchandising_promo_price"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.CheckConstraint(
            "attempt_number > 0",
            name="ck_payment_creation_attempts_number_positive",
        ),
        sa.CheckConstraint(
            "length(trim(provider)) > 0 AND provider = lower(trim(provider))",
            name="ck_payment_creation_attempts_provider_normalized",
        ),
        sa.CheckConstraint(
            "status IN ('abandoned', 'completed', 'creating', 'retry_required', 'review_required')",
            name="ck_payment_creation_attempts_status_valid",
        ),
        sa.CheckConstraint(
            "status <> 'creating' OR lease_expires_at IS NOT NULL",
            name="ck_payment_creation_attempts_creating_lease_required",
        ),
        sa.CheckConstraint(
            "status = 'creating' OR lease_expires_at IS NULL",
            name="ck_payment_creation_attempts_noncreating_lease_empty",
        ),
        sa.CheckConstraint(
            "status <> 'completed' OR length(trim(provider_payment_id)) > 0",
            name="ck_payment_creation_attempts_completed_provider_id_required",
        ),
        sa.CheckConstraint(
            "status NOT IN ('abandoned', 'retry_required', 'review_required') OR length(trim(last_error)) > 0",
            name="ck_payment_creation_attempts_failure_error_required",
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
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
    )
    op.create_index(
        "ix_payment_creation_attempts_status",
        "payment_creation_attempts",
        ["status"],
    )
    op.create_index(
        "ix_payment_creation_attempts_provider_payment_id",
        "payment_creation_attempts",
        ["provider_payment_id"],
    )
    op.create_index(
        "ix_payment_creation_attempts_lease_expires_at",
        "payment_creation_attempts",
        ["lease_expires_at"],
    )
    op.create_index(
        "uq_payment_creation_attempts_one_open",
        "payment_creation_attempts",
        ["order_id", "provider"],
        unique=True,
        postgresql_where=sa.text("status IN ('creating', 'retry_required', 'review_required')"),
    )


def downgrade() -> None:
    op.drop_index("uq_payment_creation_attempts_one_open", table_name="payment_creation_attempts")
    op.drop_index("ix_payment_creation_attempts_lease_expires_at", table_name="payment_creation_attempts")
    op.drop_index("ix_payment_creation_attempts_provider_payment_id", table_name="payment_creation_attempts")
    op.drop_index("ix_payment_creation_attempts_status", table_name="payment_creation_attempts")
    op.drop_index("ix_payment_creation_attempts_order_id", table_name="payment_creation_attempts")
    op.drop_table("payment_creation_attempts")
