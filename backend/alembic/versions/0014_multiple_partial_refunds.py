"""allow multiple partial refunds per order

Revision ID: 0014_multiple_partial_refunds
Revises: 0013_webhook_outbox_integrity
Create Date: 2026-07-29
"""

from alembic import op
import sqlalchemy as sa

revision = "0014_multiple_partial_refunds"
down_revision = "0013_webhook_outbox_integrity"
branch_labels = None
depends_on = None


def _constraint_names(table_name: str) -> set[str]:
    return {
        item.get("name")
        for item in sa.inspect(op.get_bind()).get_unique_constraints(table_name)
        if item.get("name")
    }


def upgrade():
    if "uq_return_requests_order_id" in _constraint_names("return_requests"):
        op.drop_constraint(
            "uq_return_requests_order_id",
            "return_requests",
            type_="unique",
        )


def downgrade():
    duplicate_orders = op.get_bind().execute(
        sa.text(
            """
            SELECT count(*) FROM (
                SELECT order_id
                FROM return_requests
                GROUP BY order_id
                HAVING count(*) > 1
            ) conflicts
            """
        )
    ).scalar_one()
    if duplicate_orders:
        raise RuntimeError(
            "Cannot restore one-return-per-order constraint while multiple returns exist"
        )
    if "uq_return_requests_order_id" not in _constraint_names("return_requests"):
        op.create_unique_constraint(
            "uq_return_requests_order_id",
            "return_requests",
            ["order_id"],
        )
