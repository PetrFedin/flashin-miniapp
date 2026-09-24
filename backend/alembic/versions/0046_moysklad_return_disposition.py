"""MoySklad physical-return disposition authority.

Revision ID: 0046_moysklad_return_disposition
Revises: 0045_refund_item_allocation
Create Date: 2026-09-24
"""

from alembic import op
import sqlalchemy as sa


revision = "0046_moysklad_return_disposition"
down_revision = "0045_refund_item_allocation"
branch_labels = None
depends_on = None

_DOWNGRADE_BLOCKED = (
    "0046 downgrade blocked: quarantine reclassification evidence exists; "
    "restore a verified pre-0046 backup instead of discarding terminal disposition history"
)


def upgrade() -> None:
    op.drop_constraint(
        "ck_return_logistics_events_type",
        "return_logistics_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_return_logistics_events_type",
        "return_logistics_events",
        "event_type IN ('authorized','received','inspected','reclassified')",
    )


def downgrade() -> None:
    bind = op.get_bind()
    count = int(
        bind.execute(
            sa.text(
                "SELECT count(*) FROM return_logistics_events "
                "WHERE event_type = 'reclassified'"
            )
        ).scalar_one()
    )
    if count:
        raise RuntimeError(_DOWNGRADE_BLOCKED)

    op.drop_constraint(
        "ck_return_logistics_events_type",
        "return_logistics_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_return_logistics_events_type",
        "return_logistics_events",
        "event_type IN ('authorized','received','inspected')",
    )
