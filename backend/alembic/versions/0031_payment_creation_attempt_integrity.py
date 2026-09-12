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

    # Preserve the pre-0031 YooKassa idempotency sequence. Before durable attempts,
    # the provider idempotence key used count(payments for order/provider) + 1.
    # Backfilling one terminal creation-attempt row per legacy payment guarantees
    # that the first post-migration attempt cannot reuse an already-issued key.
    op.execute(
        sa.text(
            """
            INSERT INTO payment_creation_attempts (
                order_id,
                provider,
                attempt_number,
                status,
                provider_payment_id,
                lease_expires_at,
                last_error,
                created_at,
                updated_at
            )
            SELECT
                legacy.order_id,
                'yookassa',
                legacy.attempt_number,
                CASE
                    WHEN legacy.provider_payment_id = '' THEN 'abandoned'
                    WHEN legacy.payment_status = 'canceled' THEN 'abandoned'
                    ELSE 'completed'
                END,
                legacy.provider_payment_id,
                NULL,
                CASE
                    WHEN legacy.provider_payment_id = '' THEN 'legacy_missing_provider_payment_id'
                    WHEN legacy.payment_status = 'canceled' THEN 'legacy_provider_canceled'
                    ELSE ''
                END,
                legacy.created_at,
                legacy.created_at
            FROM (
                SELECT
                    p.order_id,
                    COALESCE(trim(p.provider_payment_id), '') AS provider_payment_id,
                    lower(trim(p.status)) AS payment_status,
                    p.created_at,
                    row_number() OVER (
                        PARTITION BY p.order_id
                        ORDER BY p.id
                    ) AS attempt_number
                FROM payments AS p
                WHERE lower(trim(p.provider)) = 'yookassa'
            ) AS legacy
            ORDER BY legacy.order_id, legacy.attempt_number
            """
        )
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
