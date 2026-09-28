"""Refund item allocation authority.

Revision ID: 0045_refund_item_allocation
Revises: 0044_media_upload_commit_authority
Create Date: 2026-09-22
"""

from alembic import op
import sqlalchemy as sa


revision = "0045_refund_item_allocation"
down_revision = "0044_media_upload_commit_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "return_refund_allocations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("return_request_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("order_item_id", sa.Integer(), nullable=True),
        sa.Column("component_kind", sa.String(length=32), nullable=False),
        sa.Column("component_key", sa.String(length=96), nullable=False),
        sa.Column("quantity_evidence", sa.Integer(), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["return_request_id"],
            ["return_requests.id"],
            name="fk_return_refund_allocations_return_request_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name="fk_return_refund_allocations_order_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["order_item_id"],
            ["order_items.id"],
            name="fk_return_refund_allocations_order_item_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "return_request_id",
            "component_key",
            name="uq_return_refund_allocation_component",
        ),
        sa.CheckConstraint(
            "component_kind IN ('item','delivery','goodwill')",
            name="ck_return_refund_allocation_kind",
        ),
        sa.CheckConstraint(
            "amount_cents > 0",
            name="ck_return_refund_allocation_amount_positive",
        ),
        sa.CheckConstraint(
            "quantity_evidence IS NULL OR quantity_evidence > 0",
            name="ck_return_refund_allocation_quantity_positive",
        ),
        sa.CheckConstraint(
            "("
            "component_kind = 'item' AND order_item_id IS NOT NULL"
            ") OR ("
            "component_kind IN ('delivery','goodwill') "
            "AND order_item_id IS NULL AND quantity_evidence IS NULL"
            ")",
            name="ck_return_refund_allocation_component_shape",
        ),
        sa.CheckConstraint(
            "policy_version > 0",
            name="ck_return_refund_allocation_policy_version",
        ),
    )
    op.create_index(
        "ix_return_refund_allocations_return_request_id",
        "return_refund_allocations",
        ["return_request_id"],
    )
    op.create_index(
        "ix_return_refund_allocations_order_id",
        "return_refund_allocations",
        ["order_id"],
    )
    op.create_index(
        "ix_return_refund_allocations_order_item_id",
        "return_refund_allocations",
        ["order_item_id"],
    )
    op.create_index(
        "ix_return_refund_allocations_component_kind",
        "return_refund_allocations",
        ["component_kind"],
    )
    op.create_index(
        "ix_return_refund_allocations_created_at",
        "return_refund_allocations",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_return_refund_allocations_created_at",
        table_name="return_refund_allocations",
    )
    op.drop_index(
        "ix_return_refund_allocations_component_kind",
        table_name="return_refund_allocations",
    )
    op.drop_index(
        "ix_return_refund_allocations_order_item_id",
        table_name="return_refund_allocations",
    )
    op.drop_index(
        "ix_return_refund_allocations_order_id",
        table_name="return_refund_allocations",
    )
    op.drop_index(
        "ix_return_refund_allocations_return_request_id",
        table_name="return_refund_allocations",
    )
    op.drop_table("return_refund_allocations")
