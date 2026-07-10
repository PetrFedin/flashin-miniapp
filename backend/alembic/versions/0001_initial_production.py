"""production initial schema

Revision ID: 0001_initial_production
Revises:
Create Date: 2026-06-01

Creates production tables for FLASHIN Mini App.
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_initial_production"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "admin_users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(64), nullable=False, server_default="manager"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_admin_users_email", "admin_users", ["email"], unique=True)

    op.create_table(
        "customers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.String(64), nullable=False),
        sa.Column("username", sa.String(255), nullable=False, server_default=""),
        sa.Column("first_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("last_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("phone", sa.String(64), nullable=False, server_default=""),
        sa.Column("email", sa.String(255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_customers_telegram_id", "customers", ["telegram_id"], unique=True)

    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sku", sa.String(120), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), nullable=False),
        sa.Column("brand", sa.String(120), nullable=False, server_default="FLASHIN"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("old_price", sa.Float(), nullable=True),
        sa.Column("currency", sa.String(8), nullable=False, server_default="RUB"),
        sa.Column("category", sa.String(120), nullable=False, server_default="Clothing"),
        sa.Column("gender", sa.String(32), nullable=False, server_default="unisex"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_drop", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_rare", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("drop_starts_at", sa.DateTime(), nullable=True),
        sa.Column("vip_only_until", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_products_sku", "products", ["sku"], unique=True)
    op.create_index("ix_products_slug", "products", ["slug"], unique=True)
    op.create_index("ix_products_title", "products", ["title"])

    op.create_table(
        "media_assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(120), nullable=False, server_default=""),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_media_assets_storage_key", "media_assets", ["storage_key"])

    op.create_table(
        "promo_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("discount_type", sa.String(32), nullable=False, server_default="percent"),
        sa.Column("discount_value", sa.Float(), nullable=False),
        sa.Column("min_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("max_uses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_promo_codes_code", "promo_codes", ["code"], unique=True)

    op.create_table(
        "delivery_zones",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("delivery_type", sa.String(64), nullable=False, server_default="courier"),
        sa.Column("price", sa.Float(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_delivery_zones_name", "delivery_zones", ["name"], unique=True)

    op.create_table(
        "product_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False, server_default=""),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_product_images_product_id", "product_images", ["product_id"])

    op.create_table(
        "product_variants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("size", sa.String(32), nullable=False),
        sa.Column("color", sa.String(64), nullable=False, server_default=""),
        sa.Column("sku", sa.String(120), nullable=False),
        sa.Column("stock_qty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserved_qty", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("product_id", "size", "color", name="uq_product_size_color"),
    )
    op.create_index("ix_product_variants_product_id", "product_variants", ["product_id"])
    op.create_index("ix_product_variants_size", "product_variants", ["size"])
    op.create_index("ix_product_variants_sku", "product_variants", ["sku"], unique=True)

    op.create_table(
        "carts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("promo_code_id", sa.Integer(), sa.ForeignKey("promo_codes.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("abandoned_notified_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_carts_customer_id", "carts", ["customer_id"])

    op.create_table(
        "cart_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cart_id", sa.Integer(), sa.ForeignKey("carts.id"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("variant_id", sa.Integer(), sa.ForeignKey("product_variants.id"), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("cart_id", "variant_id", name="uq_cart_variant"),
    )
    op.create_index("ix_cart_items_cart_id", "cart_items", ["cart_id"])
    op.create_index("ix_cart_items_product_id", "cart_items", ["product_id"])
    op.create_index("ix_cart_items_variant_id", "cart_items", ["variant_id"])

    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("promo_code_id", sa.Integer(), sa.ForeignKey("promo_codes.id"), nullable=True),
        sa.Column("status", sa.String(64), nullable=False, server_default="created"),
        sa.Column("payment_status", sa.String(64), nullable=False, server_default="pending"),
        sa.Column("delivery_status", sa.String(64), nullable=False, server_default="not_started"),
        sa.Column("total_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("delivery_price", sa.Float(), nullable=False, server_default="0"),
        sa.Column("discount_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(8), nullable=False, server_default="RUB"),
        sa.Column("delivery_type", sa.String(64), nullable=False, server_default="pickup"),
        sa.Column("address", sa.Text(), nullable=False, server_default=""),
        sa.Column("comment", sa.Text(), nullable=False, server_default=""),
        sa.Column("tracking_number", sa.String(255), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_orders_customer_id", "orders", ["customer_id"])

    op.create_table(
        "order_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("variant_id", sa.Integer(), sa.ForeignKey("product_variants.id"), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("size", sa.String(32), nullable=False, server_default=""),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("price", sa.Float(), nullable=False),
    )
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])
    op.create_index("ix_order_items_product_id", "order_items", ["product_id"])
    op.create_index("ix_order_items_variant_id", "order_items", ["variant_id"])

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False, server_default="yookassa"),
        sa.Column("provider_payment_id", sa.String(255), nullable=False, server_default=""),
        sa.Column("status", sa.String(64), nullable=False, server_default="pending"),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("confirmation_url", sa.String(2048), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_payments_order_id", "payments", ["order_id"])
    op.create_index("ix_payments_provider_payment_id", "payments", ["provider_payment_id"])

    op.create_table(
        "payment_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(64), nullable=False, server_default="yookassa"),
        sa.Column("provider_payment_id", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("raw_payload", sa.Text(), nullable=False),
        sa.Column("processed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_payment_events_provider_payment_id", "payment_events", ["provider_payment_id"])
    op.create_index("ix_payment_events_event_type", "payment_events", ["event_type"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.String(64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_notifications_telegram_id", "notifications", ["telegram_id"])

    op.create_table(
        "return_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(64), nullable=False, server_default="requested"),
        sa.Column("provider_refund_id", sa.String(255), nullable=False, server_default=""),
        sa.Column("refund_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_return_requests_order_id", "return_requests", ["order_id"])
    op.create_index("ix_return_requests_customer_id", "return_requests", ["customer_id"])

    op.create_table(
        "wishlist_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("customer_id", "product_id", name="uq_customer_product_wishlist"),
    )
    op.create_index("ix_wishlist_items_customer_id", "wishlist_items", ["customer_id"])
    op.create_index("ix_wishlist_items_product_id", "wishlist_items", ["product_id"])

    op.create_table(
        "restock_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("variant_id", sa.Integer(), sa.ForeignKey("product_variants.id"), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("customer_id", "variant_id", name="uq_customer_variant_restock"),
    )
    op.create_index("ix_restock_subscriptions_customer_id", "restock_subscriptions", ["customer_id"])
    op.create_index("ix_restock_subscriptions_variant_id", "restock_subscriptions", ["variant_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("action", sa.String(120), nullable=False),
        sa.Column("entity_type", sa.String(120), nullable=False, server_default=""),
        sa.Column("entity_id", sa.String(120), nullable=False, server_default=""),
        sa.Column("payload", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])

    op.create_table(
        "inventory_adjustments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("variant_id", sa.Integer(), sa.ForeignKey("product_variants.id"), nullable=False),
        sa.Column("old_stock_qty", sa.Integer(), nullable=False),
        sa.Column("new_stock_qty", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(255), nullable=False, server_default=""),
        sa.Column("admin_id", sa.Integer(), sa.ForeignKey("admin_users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_inventory_adjustments_variant_id", "inventory_adjustments", ["variant_id"])

    op.create_table(
        "inventory_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("variant_id", sa.Integer(), sa.ForeignKey("product_variants.id"), nullable=False),
        sa.Column("stock_qty", sa.Integer(), nullable=False),
        sa.Column("reserved_qty", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(64), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_inventory_snapshots_variant_id", "inventory_snapshots", ["variant_id"])

    op.create_table(
        "analytics_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_analytics_events_event_type", "analytics_events", ["event_type"])


def downgrade():
    for table in [
        "analytics_events",
        "inventory_snapshots",
        "inventory_adjustments",
        "audit_logs",
        "restock_subscriptions",
        "wishlist_items",
        "return_requests",
        "notifications",
        "payment_events",
        "payments",
        "order_items",
        "orders",
        "cart_items",
        "carts",
        "product_variants",
        "product_images",
        "delivery_zones",
        "promo_codes",
        "media_assets",
        "products",
        "customers",
        "admin_users",
    ]:
        op.drop_table(table)
