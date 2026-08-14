"""Add durable preorder and made-to-order demand requests.

Revision ID: 0029_catalog_demand_requests
Revises: 0028_catalog_merchandising
Create Date: 2026-08-14
"""

from alembic import op
import sqlalchemy as sa


revision = "0029_catalog_demand_requests"
down_revision = "0028_catalog_merchandising"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_demand_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("variant_id", sa.Integer(), nullable=True),
        sa.Column("request_type", sa.String(length=32), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("requested_size", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("requested_color", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="requested"),
        sa.Column("admin_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("active_request_key", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "request_type IN ('preorder', 'made_to_order')",
            name="ck_product_demand_request_type",
        ),
        sa.CheckConstraint(
            "status IN ('requested', 'contacted', 'confirmed', 'cancelled')",
            name="ck_product_demand_request_status",
        ),
        sa.CheckConstraint(
            "quantity >= 1 AND quantity <= 10",
            name="ck_product_demand_request_quantity",
        ),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["variant_id"], ["product_variants.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("active_request_key", name="uq_product_demand_active_request"),
    )
    op.create_index("ix_product_demand_customer", "product_demand_requests", ["customer_id"])
    op.create_index("ix_product_demand_product", "product_demand_requests", ["product_id"])
    op.create_index("ix_product_demand_variant", "product_demand_requests", ["variant_id"])
    op.create_index("ix_product_demand_type", "product_demand_requests", ["request_type"])
    op.create_index("ix_product_demand_status", "product_demand_requests", ["status"])


def downgrade() -> None:
    op.drop_index("ix_product_demand_status", table_name="product_demand_requests")
    op.drop_index("ix_product_demand_type", table_name="product_demand_requests")
    op.drop_index("ix_product_demand_variant", table_name="product_demand_requests")
    op.drop_index("ix_product_demand_product", table_name="product_demand_requests")
    op.drop_index("ix_product_demand_customer", table_name="product_demand_requests")
    op.drop_table("product_demand_requests")
