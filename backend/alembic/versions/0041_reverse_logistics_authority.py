"""Separate financial refunds from physical reverse-logistics truth.

Revision ID: 0041_reverse_logistics_authority
Revises: 0040_delivery_authority
Create Date: 2026-09-13
"""

from alembic import op
import sqlalchemy as sa


revision = "0041_reverse_logistics_authority"
down_revision = "0040_delivery_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "return_logistics_cases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("return_request_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="requested"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "status IN ('requested','authorized','in_transit','received','inspected')",
            name="ck_return_logistics_cases_status",
        ),
        sa.ForeignKeyConstraint(["return_request_id"], ["return_requests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("return_request_id", name="uq_return_logistics_case_return_request"),
    )
    op.create_index("ix_return_logistics_cases_return_request_id", "return_logistics_cases", ["return_request_id"])
    op.create_index("ix_return_logistics_cases_order_id", "return_logistics_cases", ["order_id"])
    op.create_index("ix_return_logistics_cases_customer_id", "return_logistics_cases", ["customer_id"])
    op.create_index("ix_return_logistics_cases_status", "return_logistics_cases", ["status"])

    op.create_table(
        "return_logistics_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("order_item_id", sa.Integer(), nullable=False),
        sa.Column("variant_id", sa.Integer(), nullable=False),
        sa.Column("ordered_qty", sa.Integer(), nullable=False),
        sa.Column("authorized_qty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("received_qty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("inspected_qty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resalable_qty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("damaged_qty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quarantine_qty", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("ordered_qty > 0", name="ck_return_logistics_item_ordered_positive"),
        sa.CheckConstraint("authorized_qty >= 0 AND authorized_qty <= ordered_qty", name="ck_return_logistics_item_authorized"),
        sa.CheckConstraint("received_qty >= 0 AND received_qty <= authorized_qty", name="ck_return_logistics_item_received"),
        sa.CheckConstraint("inspected_qty >= 0 AND inspected_qty <= received_qty", name="ck_return_logistics_item_inspected"),
        sa.CheckConstraint("resalable_qty >= 0 AND damaged_qty >= 0 AND quarantine_qty >= 0", name="ck_return_logistics_item_dispositions_nonnegative"),
        sa.CheckConstraint(
            "resalable_qty + damaged_qty + quarantine_qty = inspected_qty",
            name="ck_return_logistics_item_dispositions_match_inspected",
        ),
        sa.ForeignKeyConstraint(["case_id"], ["return_logistics_cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_item_id"], ["order_items.id"]),
        sa.ForeignKeyConstraint(["variant_id"], ["product_variants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "order_item_id", name="uq_return_logistics_case_order_item"),
    )
    op.create_index("ix_return_logistics_items_case_id", "return_logistics_items", ["case_id"])
    op.create_index("ix_return_logistics_items_order_item_id", "return_logistics_items", ["order_item_id"])
    op.create_index("ix_return_logistics_items_variant_id", "return_logistics_items", ["variant_id"])

    op.create_table(
        "return_logistics_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("disposition", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("actor_admin_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("event_type IN ('authorized','received','inspected')", name="ck_return_logistics_events_type"),
        sa.CheckConstraint("disposition IN ('','resalable','damaged','quarantine')", name="ck_return_logistics_events_disposition"),
        sa.CheckConstraint("quantity > 0", name="ck_return_logistics_events_quantity_positive"),
        sa.ForeignKeyConstraint(["case_id"], ["return_logistics_cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["return_logistics_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_admin_id"], ["admin_users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", "idempotency_key", name="uq_return_logistics_item_idempotency"),
    )
    op.create_index("ix_return_logistics_events_case_id", "return_logistics_events", ["case_id"])
    op.create_index("ix_return_logistics_events_item_id", "return_logistics_events", ["item_id"])
    op.create_index("ix_return_logistics_events_type", "return_logistics_events", ["event_type"])
    op.create_index("ix_return_logistics_events_actor_admin_id", "return_logistics_events", ["actor_admin_id"])
    op.create_index("ix_return_logistics_events_created_at", "return_logistics_events", ["created_at"])

    # Preserve one movement per commercial reserve/release/commit while allowing
    # multiple legitimate physical-return receipts for the same order/variant.
    op.drop_constraint("uq_inventory_movement_order_variant_kind", "inventory_movements", type_="unique")
    op.create_index(
        "uq_inventory_movement_core_kind",
        "inventory_movements",
        ["order_id", "variant_id", "kind"],
        unique=True,
        postgresql_where=sa.text("kind IN ('reserve','release','commit')"),
    )
    op.create_index(
        "uq_inventory_movement_reverse_event_source",
        "inventory_movements",
        ["source"],
        unique=True,
        postgresql_where=sa.text("kind = 'return' AND source LIKE 'reverse_logistics_event:%'"),
    )


def downgrade() -> None:
    op.drop_index("uq_inventory_movement_reverse_event_source", table_name="inventory_movements")
    op.drop_index("uq_inventory_movement_core_kind", table_name="inventory_movements")
    # This succeeds for pre-feature/empty rollback drills. A database that has
    # already accepted multiple physical return events must be reconciled before
    # downgrading to the legacy one-return-per-order/variant model.
    op.create_unique_constraint(
        "uq_inventory_movement_order_variant_kind",
        "inventory_movements",
        ["order_id", "variant_id", "kind"],
    )

    op.drop_index("ix_return_logistics_events_created_at", table_name="return_logistics_events")
    op.drop_index("ix_return_logistics_events_actor_admin_id", table_name="return_logistics_events")
    op.drop_index("ix_return_logistics_events_type", table_name="return_logistics_events")
    op.drop_index("ix_return_logistics_events_item_id", table_name="return_logistics_events")
    op.drop_index("ix_return_logistics_events_case_id", table_name="return_logistics_events")
    op.drop_table("return_logistics_events")
    op.drop_index("ix_return_logistics_items_variant_id", table_name="return_logistics_items")
    op.drop_index("ix_return_logistics_items_order_item_id", table_name="return_logistics_items")
    op.drop_index("ix_return_logistics_items_case_id", table_name="return_logistics_items")
    op.drop_table("return_logistics_items")
    op.drop_index("ix_return_logistics_cases_status", table_name="return_logistics_cases")
    op.drop_index("ix_return_logistics_cases_customer_id", table_name="return_logistics_cases")
    op.drop_index("ix_return_logistics_cases_order_id", table_name="return_logistics_cases")
    op.drop_index("ix_return_logistics_cases_return_request_id", table_name="return_logistics_cases")
    op.drop_table("return_logistics_cases")
