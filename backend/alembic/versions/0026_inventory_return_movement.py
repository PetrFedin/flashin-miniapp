"""Allow idempotent full-refund stock restoration in inventory ledger.

Revision ID: 0026_inventory_return_movement
Revises: 0025_provider_command_outbox
Create Date: 2026-08-07
"""

from alembic import op
import sqlalchemy as sa


revision = "0026_inventory_return_movement"
down_revision = "0025_provider_command_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_inventory_movements_kind",
        "inventory_movements",
        type_="check",
    )
    op.create_check_constraint(
        "ck_inventory_movements_kind",
        "inventory_movements",
        "kind IN ('reserve', 'release', 'commit', 'return')",
    )


def downgrade() -> None:
    returned = op.get_bind().execute(
        sa.text("SELECT count(*) FROM inventory_movements WHERE kind = 'return'")
    ).scalar_one()
    if returned:
        raise RuntimeError(
            "Cannot downgrade inventory return movement support while return rows exist"
        )
    op.drop_constraint(
        "ck_inventory_movements_kind",
        "inventory_movements",
        type_="check",
    )
    op.create_check_constraint(
        "ck_inventory_movements_kind",
        "inventory_movements",
        "kind IN ('reserve', 'release', 'commit')",
    )
