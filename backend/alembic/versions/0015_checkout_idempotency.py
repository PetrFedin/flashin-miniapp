"""add checkout idempotency keys

Revision ID: 0015_checkout_idempotency
Revises: 0014_multiple_partial_refunds
Create Date: 2026-07-29
"""

from alembic import op
import sqlalchemy as sa

revision = "0015_checkout_idempotency"
down_revision = "0014_multiple_partial_refunds"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "checkout_idempotency_keys",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name="fk_checkout_idempotency_customer",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name="fk_checkout_idempotency_order",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_checkout_idempotency_keys"),
        sa.UniqueConstraint(
            "customer_id",
            "idempotency_key",
            name="uq_checkout_idempotency_customer_key",
        ),
    )
    op.create_index(
        "ix_checkout_idempotency_customer_id",
        "checkout_idempotency_keys",
        ["customer_id"],
        unique=False,
    )
    op.create_index(
        "ix_checkout_idempotency_order_id",
        "checkout_idempotency_keys",
        ["order_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        "ix_checkout_idempotency_order_id",
        table_name="checkout_idempotency_keys",
    )
    op.drop_index(
        "ix_checkout_idempotency_customer_id",
        table_name="checkout_idempotency_keys",
    )
    op.drop_table("checkout_idempotency_keys")
