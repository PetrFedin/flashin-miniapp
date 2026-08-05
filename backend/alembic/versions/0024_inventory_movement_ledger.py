"""Add durable order-linked inventory movement ledger.

Revision ID: 0024_inventory_movement_ledger
Revises: 0023_pilot_state_replay_anchor
Create Date: 2026-08-05
"""

from alembic import op
import sqlalchemy as sa


revision = "0024_inventory_movement_ledger"
down_revision = "0023_pilot_state_replay_anchor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inventory_movements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "variant_id",
            sa.Integer(),
            sa.ForeignKey("product_variants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("stock_before", sa.Integer(), nullable=False),
        sa.Column("stock_after", sa.Integer(), nullable=False),
        sa.Column("reserved_before", sa.Integer(), nullable=False),
        sa.Column("reserved_after", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('reserve', 'release', 'commit')",
            name="ck_inventory_movements_kind",
        ),
        sa.CheckConstraint(
            "quantity > 0",
            name="ck_inventory_movements_quantity_positive",
        ),
        sa.CheckConstraint(
            "stock_before >= 0 AND stock_after >= 0",
            name="ck_inventory_movements_stock_nonnegative",
        ),
        sa.CheckConstraint(
            "reserved_before >= 0 AND reserved_after >= 0",
            name="ck_inventory_movements_reserved_nonnegative",
        ),
        sa.UniqueConstraint(
            "order_id",
            "variant_id",
            "kind",
            name="uq_inventory_movement_order_variant_kind",
        ),
    )
    op.create_index(
        "ix_inventory_movements_order_id",
        "inventory_movements",
        ["order_id"],
    )
    op.create_index(
        "ix_inventory_movements_variant_id",
        "inventory_movements",
        ["variant_id"],
    )
    op.create_index(
        "ix_inventory_movements_created_at",
        "inventory_movements",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_inventory_movements_created_at",
        table_name="inventory_movements",
    )
    op.drop_index(
        "ix_inventory_movements_variant_id",
        table_name="inventory_movements",
    )
    op.drop_index(
        "ix_inventory_movements_order_id",
        table_name="inventory_movements",
    )
    op.drop_table("inventory_movements")
