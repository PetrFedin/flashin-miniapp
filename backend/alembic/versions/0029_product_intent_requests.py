"""Add non-payment preorder and made-to-order request queue.

Revision ID: 0029_product_intent_requests
Revises: 0028_catalog_merchandising
Create Date: 2026-08-15
"""

from alembic import op
import sqlalchemy as sa


revision = "0029_product_intent_requests"
down_revision = "0028_catalog_merchandising"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_intent_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("variant_id", sa.Integer(), nullable=True),
        sa.Column("intent_type", sa.String(length=32), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("requested_size", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("requested_color", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="requested"),
        sa.Column("admin_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("quote_amount", sa.Float(), nullable=True),
        sa.Column("quote_currency", sa.String(length=8), nullable=False, server_default="RUB"),
        sa.Column("estimated_ready_at", sa.DateTime(), nullable=True),
        sa.Column("active_request_key", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "intent_type IN ('preorder', 'made_to_order')",
            name="ck_product_intent_requests_type",
        ),
        sa.CheckConstraint(
            "status IN ('requested', 'working', 'ready', 'closed', 'cancelled')",
            name="ck_product_intent_requests_status",
        ),
        sa.CheckConstraint(
            "quantity >= 1 AND quantity <= 5",
            name="ck_product_intent_requests_quantity",
        ),
        sa.CheckConstraint(
            "quote_amount IS NULL OR quote_amount >= 0",
            name="ck_product_intent_requests_quote_amount",
        ),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["variant_id"], ["product_variants.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("active_request_key", name="uq_product_intent_requests_active_key"),
    )
    op.create_index(
        "ix_product_intent_requests_customer_id",
        "product_intent_requests",
        ["customer_id"],
    )
    op.create_index(
        "ix_product_intent_requests_product_id",
        "product_intent_requests",
        ["product_id"],
    )
    op.create_index(
        "ix_product_intent_requests_variant_id",
        "product_intent_requests",
        ["variant_id"],
    )
    op.create_index(
        "ix_product_intent_requests_intent_type",
        "product_intent_requests",
        ["intent_type"],
    )
    op.create_index(
        "ix_product_intent_requests_status",
        "product_intent_requests",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_product_intent_requests_status", table_name="product_intent_requests")
    op.drop_index("ix_product_intent_requests_intent_type", table_name="product_intent_requests")
    op.drop_index("ix_product_intent_requests_variant_id", table_name="product_intent_requests")
    op.drop_index("ix_product_intent_requests_product_id", table_name="product_intent_requests")
    op.drop_index("ix_product_intent_requests_customer_id", table_name="product_intent_requests")
    op.drop_table("product_intent_requests")
