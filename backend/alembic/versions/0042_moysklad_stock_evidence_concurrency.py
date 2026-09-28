"""Make MoySklad stock-authority evidence concurrency-safe.

Revision ID: 0042_moysklad_stock_evidence_concurrency
Revises: 0041_reverse_logistics_authority
Create Date: 2026-09-17
"""

from alembic import op
import sqlalchemy as sa


revision = "0042_moysklad_stock_evidence_concurrency"
down_revision = "0041_reverse_logistics_authority"
branch_labels = None
depends_on = None

_STALE_CONFLICT = "stale_stock_pending_physical_return"
_BLOCKED_ACTION = "blocked_physical_return"


def _collapse_duplicate_open_evidence() -> None:
    """Preserve history while making pre-0042 duplicate open rows indexable."""

    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    id,
                    row_number() OVER (
                        PARTITION BY moysklad_id, conflict_type
                        ORDER BY created_at DESC, id DESC
                    ) AS rn
                FROM moysklad_conflicts
                WHERE status = 'open'
                  AND conflict_type = :conflict_type
            )
            UPDATE moysklad_conflicts
               SET status = 'resolved'
             WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
            """
        ),
        {"conflict_type": _STALE_CONFLICT},
    )
    bind.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT
                    id,
                    row_number() OVER (
                        PARTITION BY variant_id
                        ORDER BY created_at DESC, id DESC
                    ) AS rn
                FROM stock_reconciliation_logs
                WHERE status = 'open'
                  AND action = :action
            )
            UPDATE stock_reconciliation_logs
               SET status = 'resolved'
             WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
            """
        ),
        {"action": _BLOCKED_ACTION},
    )


def upgrade() -> None:
    _collapse_duplicate_open_evidence()

    conflict_predicate = sa.text(
        "status = 'open' AND conflict_type = 'stale_stock_pending_physical_return'"
    )
    reconciliation_predicate = sa.text(
        "status = 'open' AND action = 'blocked_physical_return'"
    )
    op.create_index(
        "uq_moysklad_conflict_open_stale_physical_return",
        "moysklad_conflicts",
        ["moysklad_id", "conflict_type"],
        unique=True,
        postgresql_where=conflict_predicate,
        sqlite_where=conflict_predicate,
    )
    op.create_index(
        "uq_stock_reconciliation_open_blocked_physical_return",
        "stock_reconciliation_logs",
        ["variant_id"],
        unique=True,
        postgresql_where=reconciliation_predicate,
        sqlite_where=reconciliation_predicate,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_stock_reconciliation_open_blocked_physical_return",
        table_name="stock_reconciliation_logs",
    )
    op.drop_index(
        "uq_moysklad_conflict_open_stale_physical_return",
        table_name="moysklad_conflicts",
    )
