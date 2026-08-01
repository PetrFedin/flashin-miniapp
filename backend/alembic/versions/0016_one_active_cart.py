"""enforce one active cart per customer

Revision ID: 0016_one_active_cart
Revises: 0015_checkout_idempotency
Create Date: 2026-08-01
"""

from alembic import op
import sqlalchemy as sa

revision = "0016_one_active_cart"
down_revision = "0015_checkout_idempotency"
branch_labels = None
depends_on = None


_ACTIVE_CART_INDEX = "uq_carts_customer_active"


def upgrade():
    op.execute(
        sa.text(
            """
            WITH ranked_active_carts AS (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY customer_id
                        ORDER BY updated_at DESC NULLS LAST, created_at DESC, id DESC
                    ) AS row_number
                FROM carts
                WHERE status = 'active'
            )
            UPDATE carts
            SET status = 'superseded'
            WHERE id IN (
                SELECT id
                FROM ranked_active_carts
                WHERE row_number > 1
            )
            """
        )
    )
    op.create_index(
        _ACTIVE_CART_INDEX,
        "carts",
        ["customer_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade():
    op.drop_index(_ACTIVE_CART_INDEX, table_name="carts")
