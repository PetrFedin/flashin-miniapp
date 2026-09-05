"""Enforce residual relational evidence integrity.

Revision ID: 0038_relational_evidence_integrity
Revises: 0037_product_variant_reference_integrity
Create Date: 2026-08-28

The application already treats these links as intrinsic identities:

* a reconciliation row that names both a local payment and local order must
  point at that payment's order; provider status/amount mismatches remain
  representable and are intentionally unaffected;
* a fulfillment task item must reference an order item from the same order as
  its fulfillment task.

Existing rows are checked first. The migration never rewrites or deletes
financial, reconciliation, fulfillment, or customer evidence to make the new
rules pass.
"""

from alembic import op
import sqlalchemy as sa


revision = "0038_relational_evidence_integrity"
down_revision = "0037_product_variant_reference_integrity"
branch_labels = None
depends_on = None


_FULFILLMENT_TRIGGER = "trg_fulfillment_task_items_same_order"
_FULFILLMENT_FUNCTION = "enforce_fulfillment_task_item_same_order"


def _assert_empty(sql: str, message: str) -> None:
    count = op.get_bind().execute(sa.text(sql)).scalar_one()
    if count:
        raise RuntimeError(
            f"Integrity migration blocked: {message} ({count} conflicting row(s))"
        )


def _validate_existing_data() -> None:
    _assert_empty(
        """
        SELECT count(*)
        FROM payment_reconciliations reconciliation
        JOIN payments payment ON payment.id = reconciliation.payment_id
        WHERE reconciliation.payment_id IS NOT NULL
          AND reconciliation.order_id IS NOT NULL
          AND reconciliation.order_id <> payment.order_id
        """,
        "payment reconciliations reference an order different from their local payment",
    )
    _assert_empty(
        """
        SELECT count(*)
        FROM fulfillment_task_items task_item
        JOIN fulfillment_tasks task ON task.id = task_item.task_id
        JOIN order_items order_item ON order_item.id = task_item.order_item_id
        WHERE task.order_id <> order_item.order_id
        """,
        "fulfillment task items reference order items from another order",
    )


def upgrade() -> None:
    _validate_existing_data()

    op.create_unique_constraint(
        "uq_payments_id_order_id",
        "payments",
        ["id", "order_id"],
    )
    op.create_foreign_key(
        "fk_payment_reconciliations_payment_order",
        "payment_reconciliations",
        "payments",
        ["payment_id", "order_id"],
        ["id", "order_id"],
    )

    op.execute(
        sa.text(
            f"""
            CREATE OR REPLACE FUNCTION {_FULFILLMENT_FUNCTION}()
            RETURNS trigger AS $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM fulfillment_tasks task
                    JOIN order_items order_item
                      ON order_item.id = NEW.order_item_id
                    WHERE task.id = NEW.task_id
                      AND task.order_id = order_item.order_id
                ) THEN
                    RAISE EXCEPTION 'fulfillment task item must reference an order item from the same order';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE TRIGGER {_FULFILLMENT_TRIGGER}
            BEFORE INSERT OR UPDATE OF task_id, order_item_id
            ON fulfillment_task_items
            FOR EACH ROW
            EXECUTE FUNCTION {_FULFILLMENT_FUNCTION}()
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            f"DROP TRIGGER IF EXISTS {_FULFILLMENT_TRIGGER} ON fulfillment_task_items"
        )
    )
    op.execute(sa.text(f"DROP FUNCTION IF EXISTS {_FULFILLMENT_FUNCTION}()"))
    op.drop_constraint(
        "fk_payment_reconciliations_payment_order",
        "payment_reconciliations",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_payments_id_order_id",
        "payments",
        type_="unique",
    )
