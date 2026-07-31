"""add durable checkout idempotency

Revision ID: 0015_checkout_idempotency
Revises: 0014_multiple_partial_refunds
Create Date: 2026-07-31
"""

from alembic import op
import sqlalchemy as sa

revision = "0015_checkout_idempotency"
down_revision = "0014_multiple_partial_refunds"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "checkout_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("cart_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["cart_id"], ["carts.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cart_id", name="uq_checkout_attempt_cart"),
        sa.UniqueConstraint(
            "customer_id",
            "idempotency_key",
            name="uq_checkout_attempt_customer_key",
        ),
        sa.UniqueConstraint("order_id", name="uq_checkout_attempt_order"),
    )
    op.create_index(
        op.f("ix_checkout_attempts_customer_id"),
        "checkout_attempts",
        ["customer_id"],
        unique=False,
    )


def downgrade():
    op.drop_index(
        op.f("ix_checkout_attempts_customer_id"),
        table_name="checkout_attempts",
    )
    op.drop_table("checkout_attempts")
