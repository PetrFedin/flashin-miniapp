"""reconciliation campaign timeline loyalty holds

Revision ID: 0006_reconciliation_campaign_timeline_loyalty_holds
Revises: 0005_hardening_moysklad_loyalty_meili_metrics
Create Date: 2026-06-01
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_reconciliation_campaign_timeline_loyalty_holds"
down_revision = "0005_hardening_moysklad_loyalty_meili_metrics"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("marketing_campaigns", sa.Column("scheduled_at", sa.DateTime(), nullable=True))

    op.create_table(
        "stock_reconciliation_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("variant_id", sa.Integer(), sa.ForeignKey("product_variants.id"), nullable=False),
        sa.Column("sku", sa.String(120), nullable=False, server_default=""),
        sa.Column("local_stock_qty", sa.Integer(), nullable=False),
        sa.Column("external_stock_qty", sa.Integer(), nullable=False),
        sa.Column("local_reserved_qty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("action", sa.String(64), nullable=False, server_default="report"),
        sa.Column("status", sa.String(64), nullable=False, server_default="open"),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_stock_reconciliation_logs_variant_id", "stock_reconciliation_logs", ["variant_id"])

    op.create_table(
        "loyalty_redemption_holds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("cart_id", sa.Integer(), sa.ForeignKey("carts.id"), nullable=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=True),
        sa.Column("points", sa.Float(), nullable=False),
        sa.Column("status", sa.String(64), nullable=False, server_default="reserved"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("released_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_loyalty_redemption_holds_customer_id", "loyalty_redemption_holds", ["customer_id"])


def downgrade():
    op.drop_table("loyalty_redemption_holds")
    op.drop_table("stock_reconciliation_logs")
    op.drop_column("marketing_campaigns", "scheduled_at")
