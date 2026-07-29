"""refund integrity and loyalty reversal idempotency

Revision ID: 0011_refund_integrity_and_loyalty_reversals
Revises: 0010_transaction_integrity_constraints
Create Date: 2026-07-29

This migration is intentionally forward-safe: it adds return constraints only
when they are missing, so deployments that already ran an earlier form of 0010
are upgraded to the same final schema as fresh installations.
"""

from alembic import op
import sqlalchemy as sa

revision = "0011_refund_integrity_and_loyalty_reversals"
down_revision = "0010_transaction_integrity_constraints"
branch_labels = None
depends_on = None

_REVERSAL_REASONS = (
    "order_paid",
    "loyalty_redeemed",
    "referral_reward",
    "loyalty_refund",
    "order_refund_reversal",
    "referral_refund_reversal",
)


def _assert_empty(sql: str, message: str) -> None:
    count = op.get_bind().execute(sa.text(sql)).scalar_one()
    if count:
        raise RuntimeError(f"Refund integrity migration blocked: {message} ({count} conflict group(s)/row(s))")


def _constraint_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    names = {item.get("name") for item in inspector.get_unique_constraints(table_name)}
    names.update(item.get("name") for item in inspector.get_check_constraints(table_name))
    return {name for name in names if name}


def _index_names(table_name: str) -> set[str]:
    return {
        item.get("name")
        for item in sa.inspect(op.get_bind()).get_indexes(table_name)
        if item.get("name")
    }


def upgrade():
    _assert_empty(
        "SELECT count(*) FROM return_requests WHERE refund_amount < 0",
        "return requests contain negative refund amounts",
    )
    _assert_empty(
        """
        SELECT count(*) FROM (
            SELECT order_id FROM return_requests
            GROUP BY order_id HAVING count(*) > 1
        ) conflicts
        """,
        "orders have duplicate return requests",
    )
    _assert_empty(
        """
        SELECT count(*) FROM (
            SELECT provider_refund_id FROM return_requests
            WHERE provider_refund_id <> ''
            GROUP BY provider_refund_id HAVING count(*) > 1
        ) conflicts
        """,
        "provider refund IDs are duplicated",
    )
    _assert_empty(
        """
        SELECT count(*) FROM (
            SELECT customer_id, order_id, reason
            FROM loyalty_transactions
            WHERE order_id IS NOT NULL
              AND reason IN (
                  'order_paid',
                  'loyalty_redeemed',
                  'referral_reward',
                  'loyalty_refund',
                  'order_refund_reversal',
                  'referral_refund_reversal'
              )
            GROUP BY customer_id, order_id, reason
            HAVING count(*) > 1
        ) conflicts
        """,
        "order-linked loyalty transactions or refund reversals are duplicated",
    )

    return_constraints = _constraint_names("return_requests")
    if "ck_return_requests_refund_nonnegative" not in return_constraints:
        op.create_check_constraint(
            "ck_return_requests_refund_nonnegative",
            "return_requests",
            "refund_amount >= 0",
        )
    if "uq_return_requests_order_id" not in return_constraints:
        op.create_unique_constraint(
            "uq_return_requests_order_id",
            "return_requests",
            ["order_id"],
        )

    return_indexes = _index_names("return_requests")
    if "uq_return_requests_provider_refund_id" not in return_indexes:
        op.create_index(
            "uq_return_requests_provider_refund_id",
            "return_requests",
            ["provider_refund_id"],
            unique=True,
            postgresql_where=sa.text("provider_refund_id <> ''"),
        )

    loyalty_indexes = _index_names("loyalty_transactions")
    if "uq_loyalty_transactions_order_reason" in loyalty_indexes:
        op.drop_index(
            "uq_loyalty_transactions_order_reason",
            table_name="loyalty_transactions",
        )
    op.create_index(
        "uq_loyalty_transactions_order_reason",
        "loyalty_transactions",
        ["customer_id", "order_id", "reason"],
        unique=True,
        postgresql_where=sa.text(
            "order_id IS NOT NULL AND reason IN ("
            "'order_paid', 'loyalty_redeemed', 'referral_reward', 'loyalty_refund', "
            "'order_refund_reversal', 'referral_refund_reversal'"
            ")"
        ),
    )


def downgrade():
    loyalty_indexes = _index_names("loyalty_transactions")
    if "uq_loyalty_transactions_order_reason" in loyalty_indexes:
        op.drop_index(
            "uq_loyalty_transactions_order_reason",
            table_name="loyalty_transactions",
        )
    op.create_index(
        "uq_loyalty_transactions_order_reason",
        "loyalty_transactions",
        ["customer_id", "order_id", "reason"],
        unique=True,
        postgresql_where=sa.text(
            "order_id IS NOT NULL AND reason IN "
            "('order_paid', 'loyalty_redeemed', 'referral_reward', 'loyalty_refund')"
        ),
    )

    if "uq_return_requests_provider_refund_id" in _index_names("return_requests"):
        op.drop_index(
            "uq_return_requests_provider_refund_id",
            table_name="return_requests",
        )
    return_constraints = _constraint_names("return_requests")
    if "uq_return_requests_order_id" in return_constraints:
        op.drop_constraint(
            "uq_return_requests_order_id",
            "return_requests",
            type_="unique",
        )
    if "ck_return_requests_refund_nonnegative" in return_constraints:
        op.drop_constraint(
            "ck_return_requests_refund_nonnegative",
            "return_requests",
            type_="check",
        )
