"""moysklad crm recommendations

Revision ID: 0003_moysklad_crm_recs
Revises: 0002_support_privacy_outbox
Create Date: 2026-06-01
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_moysklad_crm_recs"
down_revision = "0002_support_privacy_outbox"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("products", sa.Column("moysklad_id", sa.String(255), nullable=False, server_default=""))
    op.create_index("ix_products_moysklad_id", "products", ["moysklad_id"])

    op.add_column("product_variants", sa.Column("moysklad_id", sa.String(255), nullable=False, server_default=""))
    op.create_index("ix_product_variants_moysklad_id", "product_variants", ["moysklad_id"])

    op.create_table(
        "moysklad_sync_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sync_type", sa.String(64), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(64), nullable=False, server_default="started"),
        sa.Column("products_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("products_upserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("variants_upserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "crm_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("segment", sa.String(120), nullable=False, server_default="new"),
        sa.Column("orders_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_spent", sa.Float(), nullable=False, server_default="0"),
        sa.Column("average_order_value", sa.Float(), nullable=False, server_default="0"),
        sa.Column("last_order_at", sa.DateTime(), nullable=True),
        sa.Column("loyalty_points", sa.Float(), nullable=False, server_default="0"),
        sa.Column("vip", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_crm_profiles_customer_id", "crm_profiles", ["customer_id"], unique=True)

    op.create_table(
        "product_recommendations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("recommended_product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("score", sa.Float(), nullable=False, server_default="1"),
        sa.Column("source", sa.String(64), nullable=False, server_default="manual"),
    )
    op.create_index("ix_product_recommendations_product_id", "product_recommendations", ["product_id"])
    op.create_index("ix_product_recommendations_recommended_product_id", "product_recommendations", ["recommended_product_id"])


def downgrade():
    op.drop_table("product_recommendations")
    op.drop_table("crm_profiles")
    op.drop_table("moysklad_sync_logs")
    op.drop_index("ix_product_variants_moysklad_id", table_name="product_variants")
    op.drop_column("product_variants", "moysklad_id")
    op.drop_index("ix_products_moysklad_id", table_name="products")
    op.drop_column("products", "moysklad_id")
