"""Add scheduled merchandising promo price.

Revision ID: 0030_merchandising_promo_price
Revises: 0029_product_intent_requests
Create Date: 2026-08-17
"""

from alembic import op
import sqlalchemy as sa


revision = "0030_merchandising_promo_price"
down_revision = "0029_product_intent_requests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "product_merchandising",
        sa.Column("promo_price", sa.Float(), nullable=True),
    )
    op.create_check_constraint(
        "ck_product_merchandising_promo_price",
        "product_merchandising",
        "promo_price IS NULL OR promo_price > 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_product_merchandising_promo_price",
        "product_merchandising",
        type_="check",
    )
    op.drop_column("product_merchandising", "promo_price")
