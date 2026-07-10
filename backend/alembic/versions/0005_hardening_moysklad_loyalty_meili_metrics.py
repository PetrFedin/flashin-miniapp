"""hardening moysklad loyalty meili metrics

Revision ID: 0005_hardening_moysklad_loyalty_meili_metrics
Revises: 0004_growth_marketing_search_looks
Create Date: 2026-06-01
"""
from alembic import op
import sqlalchemy as sa

revision = "0005_hardening_moysklad_loyalty_meili_metrics"
down_revision = "0004_growth_marketing_search_looks"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("carts", sa.Column("referral_code", sa.String(64), nullable=False, server_default=""))
    op.add_column("carts", sa.Column("loyalty_points_to_redeem", sa.Float(), nullable=False, server_default="0"))

    op.add_column("orders", sa.Column("loyalty_points_redeemed", sa.Float(), nullable=False, server_default="0"))
    op.add_column("orders", sa.Column("loyalty_discount_amount", sa.Float(), nullable=False, server_default="0"))
    op.add_column("orders", sa.Column("referral_code", sa.String(64), nullable=False, server_default=""))

    op.create_table(
        "moysklad_mapping_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_field", sa.String(120), nullable=False),
        sa.Column("source_value", sa.String(255), nullable=False),
        sa.Column("target_field", sa.String(120), nullable=False),
        sa.Column("target_value", sa.String(255), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )

    op.create_table(
        "moysklad_conflicts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("moysklad_id", sa.String(255), nullable=False),
        sa.Column("sku", sa.String(120), nullable=False, server_default=""),
        sa.Column("conflict_type", sa.String(120), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(64), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_moysklad_conflicts_moysklad_id", "moysklad_conflicts", ["moysklad_id"])

    op.create_table(
        "referral_attributions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("referral_code_id", sa.Integer(), sa.ForeignKey("referral_codes.id"), nullable=False),
        sa.Column("invited_customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("status", sa.String(64), nullable=False, server_default="pending"),
        sa.Column("rewarded_order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("rewarded_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_referral_attributions_referral_code_id", "referral_attributions", ["referral_code_id"])
    op.create_index("ix_referral_attributions_invited_customer_id", "referral_attributions", ["invited_customer_id"], unique=True)


def downgrade():
    op.drop_table("referral_attributions")
    op.drop_table("moysklad_conflicts")
    op.drop_table("moysklad_mapping_rules")
    op.drop_column("orders", "referral_code")
    op.drop_column("orders", "loyalty_discount_amount")
    op.drop_column("orders", "loyalty_points_redeemed")
    op.drop_column("carts", "loyalty_points_to_redeem")
    op.drop_column("carts", "referral_code")
