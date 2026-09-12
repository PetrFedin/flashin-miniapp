"""Add authoritative delivery quotes and shipment commercial bindings.

Revision ID: 0040_delivery_authority
Revises: 0039_customer_auth_state
Create Date: 2026-09-12
"""

from alembic import op
import sqlalchemy as sa


revision = "0040_delivery_authority"
down_revision = "0039_customer_auth_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "delivery_zone_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("zone_id", sa.Integer(), nullable=False),
        sa.Column("delivery_type", sa.String(length=64), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("region", sa.String(length=160), nullable=False),
        sa.Column("city", sa.String(length=160), nullable=False),
        sa.Column("postal_prefix", sa.String(length=32), nullable=False),
        sa.Column("provider_code", sa.String(length=64), nullable=False),
        sa.Column("service_code", sa.String(length=96), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("priority >= 0", name="ck_delivery_zone_rules_priority_nonnegative"),
        sa.CheckConstraint("version >= 1", name="ck_delivery_zone_rules_version_positive"),
        sa.ForeignKeyConstraint(["zone_id"], ["delivery_zones.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("zone_id", name="uq_delivery_zone_rules_zone_id"),
        sa.UniqueConstraint("delivery_type", "priority", name="uq_delivery_zone_rules_type_priority"),
    )
    op.create_index("ix_delivery_zone_rules_zone_id", "delivery_zone_rules", ["zone_id"], unique=False)
    op.create_index("ix_delivery_zone_rules_delivery_type", "delivery_zone_rules", ["delivery_type"], unique=False)

    op.create_table(
        "delivery_quotes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("public_id", sa.String(length=80), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("zone_id", sa.Integer(), nullable=True),
        sa.Column("delivery_type", sa.String(length=64), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("region", sa.String(length=160), nullable=False),
        sa.Column("city", sa.String(length=160), nullable=False),
        sa.Column("postal_code", sa.String(length=32), nullable=False),
        sa.Column("address_line", sa.String(length=500), nullable=False),
        sa.Column("address_snapshot", sa.String(length=700), nullable=False),
        sa.Column("provider_code", sa.String(length=64), nullable=False),
        sa.Column("service_code", sa.String(length=96), nullable=False),
        sa.Column("price", sa.Numeric(precision=20, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("zone_version", sa.Integer(), nullable=False),
        sa.Column("quote_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("price >= 0", name="ck_delivery_quotes_price_nonnegative"),
        sa.CheckConstraint("status IN ('created','accepted','expired','cancelled')", name="ck_delivery_quotes_status"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["zone_id"], ["delivery_zones.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", name="uq_delivery_quotes_order_id"),
        sa.UniqueConstraint("public_id", name="uq_delivery_quotes_public_id"),
    )
    op.create_index("ix_delivery_quotes_public_id", "delivery_quotes", ["public_id"], unique=False)
    op.create_index("ix_delivery_quotes_customer_id", "delivery_quotes", ["customer_id"], unique=False)
    op.create_index("ix_delivery_quotes_zone_id", "delivery_quotes", ["zone_id"], unique=False)
    op.create_index("ix_delivery_quotes_status", "delivery_quotes", ["status"], unique=False)
    op.create_index("ix_delivery_quotes_expires_at", "delivery_quotes", ["expires_at"], unique=False)
    op.create_index("ix_delivery_quotes_order_id", "delivery_quotes", ["order_id"], unique=False)

    op.create_table(
        "delivery_shipment_authorities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("shipment_id", sa.Integer(), nullable=False),
        sa.Column("quote_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("zone_id", sa.Integer(), nullable=True),
        sa.Column("provider_code", sa.String(length=64), nullable=False),
        sa.Column("service_code", sa.String(length=96), nullable=False),
        sa.Column("price", sa.Numeric(precision=20, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("price >= 0", name="ck_delivery_shipment_authorities_price_nonnegative"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["quote_id"], ["delivery_quotes.id"]),
        sa.ForeignKeyConstraint(["shipment_id"], ["delivery_shipments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["zone_id"], ["delivery_zones.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", name="uq_delivery_shipment_authorities_order"),
        sa.UniqueConstraint("quote_id", name="uq_delivery_shipment_authorities_quote"),
        sa.UniqueConstraint("shipment_id", name="uq_delivery_shipment_authorities_shipment"),
    )
    op.create_index("ix_delivery_shipment_authorities_shipment_id", "delivery_shipment_authorities", ["shipment_id"], unique=False)
    op.create_index("ix_delivery_shipment_authorities_quote_id", "delivery_shipment_authorities", ["quote_id"], unique=False)
    op.create_index("ix_delivery_shipment_authorities_order_id", "delivery_shipment_authorities", ["order_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_delivery_shipment_authorities_order_id", table_name="delivery_shipment_authorities")
    op.drop_index("ix_delivery_shipment_authorities_quote_id", table_name="delivery_shipment_authorities")
    op.drop_index("ix_delivery_shipment_authorities_shipment_id", table_name="delivery_shipment_authorities")
    op.drop_table("delivery_shipment_authorities")

    op.drop_index("ix_delivery_quotes_order_id", table_name="delivery_quotes")
    op.drop_index("ix_delivery_quotes_expires_at", table_name="delivery_quotes")
    op.drop_index("ix_delivery_quotes_status", table_name="delivery_quotes")
    op.drop_index("ix_delivery_quotes_zone_id", table_name="delivery_quotes")
    op.drop_index("ix_delivery_quotes_customer_id", table_name="delivery_quotes")
    op.drop_index("ix_delivery_quotes_public_id", table_name="delivery_quotes")
    op.drop_table("delivery_quotes")

    op.drop_index("ix_delivery_zone_rules_delivery_type", table_name="delivery_zone_rules")
    op.drop_index("ix_delivery_zone_rules_zone_id", table_name="delivery_zone_rules")
    op.drop_table("delivery_zone_rules")
