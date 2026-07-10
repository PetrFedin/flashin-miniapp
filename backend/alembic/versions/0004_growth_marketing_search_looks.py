"""growth marketing search looks loyalty

Revision ID: 0004_growth_marketing_search_looks
Revises: 0003_moysklad_crm_recs
Create Date: 2026-06-01
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_growth_marketing_search_looks"
down_revision = "0003_moysklad_crm_recs"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "loyalty_transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=True),
        sa.Column("points_delta", sa.Float(), nullable=False),
        sa.Column("reason", sa.String(255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_loyalty_transactions_customer_id", "loyalty_transactions", ["customer_id"])

    op.create_table(
        "referral_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("reward_points", sa.Float(), nullable=False, server_default="500"),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_referral_codes_customer_id", "referral_codes", ["customer_id"])
    op.create_index("ix_referral_codes_code", "referral_codes", ["code"], unique=True)

    op.create_table(
        "marketing_campaigns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("segment", sa.String(120), nullable=False, server_default="all"),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(64), nullable=False, server_default="draft"),
        sa.Column("sent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
    )

    op.create_table(
        "product_search_index",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_product_search_index_product_id", "product_search_index", ["product_id"], unique=True)

    op.create_table(
        "looks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "look_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("look_id", sa.Integer(), sa.ForeignKey("looks.id"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_look_items_look_id", "look_items", ["look_id"])
    op.create_index("ix_look_items_product_id", "look_items", ["product_id"])

    op.create_table(
        "customer_timeline_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_customer_timeline_events_customer_id", "customer_timeline_events", ["customer_id"])
    op.create_index("ix_customer_timeline_events_event_type", "customer_timeline_events", ["event_type"])


def downgrade():
    op.drop_table("customer_timeline_events")
    op.drop_table("look_items")
    op.drop_table("looks")
    op.drop_table("product_search_index")
    op.drop_table("marketing_campaigns")
    op.drop_table("referral_codes")
    op.drop_table("loyalty_transactions")
