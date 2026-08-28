"""Enforce customer ownership for order/cart references.

Revision ID: 0036_customer_owned_reference_integrity
Revises: 0035_fulfillment_read_permission
"""

from collections.abc import Iterable

from alembic import op
import sqlalchemy as sa


revision = "0036_customer_owned_reference_integrity"
down_revision = "0035_fulfillment_read_permission"
branch_labels = None
depends_on = None


OwnershipCheck = tuple[str, str]


OWNERSHIP_CHECKS: tuple[OwnershipCheck, ...] = (
    (
        "return_requests.order_id/customer_id",
        """
        SELECT count(*)
        FROM return_requests AS child
        JOIN orders AS parent ON parent.id = child.order_id
        WHERE child.customer_id <> parent.customer_id
        """,
    ),
    (
        "support_tickets.order_id/customer_id",
        """
        SELECT count(*)
        FROM support_tickets AS child
        JOIN orders AS parent ON parent.id = child.order_id
        WHERE child.order_id IS NOT NULL
          AND child.customer_id IS NOT NULL
          AND child.customer_id <> parent.customer_id
        """,
    ),
    (
        "loyalty_redemption_holds.order_id/customer_id",
        """
        SELECT count(*)
        FROM loyalty_redemption_holds AS child
        JOIN orders AS parent ON parent.id = child.order_id
        WHERE child.order_id IS NOT NULL
          AND child.customer_id <> parent.customer_id
        """,
    ),
    (
        "loyalty_redemption_holds.cart_id/customer_id",
        """
        SELECT count(*)
        FROM loyalty_redemption_holds AS child
        JOIN carts AS parent ON parent.id = child.cart_id
        WHERE child.cart_id IS NOT NULL
          AND child.customer_id <> parent.customer_id
        """,
    ),
    (
        "checkout_attempts.order_id/customer_id",
        """
        SELECT count(*)
        FROM checkout_attempts AS child
        JOIN orders AS parent ON parent.id = child.order_id
        WHERE child.order_id IS NOT NULL
          AND child.customer_id <> parent.customer_id
        """,
    ),
    (
        "checkout_attempts.cart_id/customer_id",
        """
        SELECT count(*)
        FROM checkout_attempts AS child
        JOIN carts AS parent ON parent.id = child.cart_id
        WHERE child.customer_id <> parent.customer_id
        """,
    ),
    (
        "pilot_order_slots.order_id/customer_id",
        """
        SELECT count(*)
        FROM pilot_order_slots AS child
        JOIN orders AS parent ON parent.id = child.order_id
        WHERE child.customer_id <> parent.customer_id
        """,
    ),
)


def _find_ownership_mismatches(bind, checks: Iterable[OwnershipCheck] = OWNERSHIP_CHECKS) -> list[str]:
    failures: list[str] = []
    for label, query in checks:
        count = int(bind.execute(sa.text(query)).scalar_one())
        if count:
            failures.append(f"{label}={count}")
    return failures


def _assert_customer_owned_references_are_clean(bind) -> None:
    failures = _find_ownership_mismatches(bind)
    if failures:
        joined = ", ".join(failures)
        raise RuntimeError(
            "Customer-owned reference integrity preflight failed; "
            "migration will not rewrite or delete existing records: "
            f"{joined}"
        )


def upgrade() -> None:
    bind = op.get_bind()
    _assert_customer_owned_references_are_clean(bind)

    op.create_unique_constraint(
        "uq_orders_id_customer_id",
        "orders",
        ["id", "customer_id"],
    )
    op.create_unique_constraint(
        "uq_carts_id_customer_id",
        "carts",
        ["id", "customer_id"],
    )

    op.create_foreign_key(
        "fk_return_requests_order_customer",
        "return_requests",
        "orders",
        ["order_id", "customer_id"],
        ["id", "customer_id"],
    )
    op.create_foreign_key(
        "fk_support_tickets_order_customer",
        "support_tickets",
        "orders",
        ["order_id", "customer_id"],
        ["id", "customer_id"],
    )
    op.create_foreign_key(
        "fk_loyalty_redemption_holds_order_customer",
        "loyalty_redemption_holds",
        "orders",
        ["order_id", "customer_id"],
        ["id", "customer_id"],
    )
    op.create_foreign_key(
        "fk_loyalty_redemption_holds_cart_customer",
        "loyalty_redemption_holds",
        "carts",
        ["cart_id", "customer_id"],
        ["id", "customer_id"],
    )
    op.create_foreign_key(
        "fk_checkout_attempts_order_customer",
        "checkout_attempts",
        "orders",
        ["order_id", "customer_id"],
        ["id", "customer_id"],
    )
    op.create_foreign_key(
        "fk_checkout_attempts_cart_customer",
        "checkout_attempts",
        "carts",
        ["cart_id", "customer_id"],
        ["id", "customer_id"],
    )
    op.create_foreign_key(
        "fk_pilot_order_slots_order_customer",
        "pilot_order_slots",
        "orders",
        ["order_id", "customer_id"],
        ["id", "customer_id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_pilot_order_slots_order_customer",
        "pilot_order_slots",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_checkout_attempts_cart_customer",
        "checkout_attempts",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_checkout_attempts_order_customer",
        "checkout_attempts",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_loyalty_redemption_holds_cart_customer",
        "loyalty_redemption_holds",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_loyalty_redemption_holds_order_customer",
        "loyalty_redemption_holds",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_support_tickets_order_customer",
        "support_tickets",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_return_requests_order_customer",
        "return_requests",
        type_="foreignkey",
    )

    op.drop_constraint("uq_carts_id_customer_id", "carts", type_="unique")
    op.drop_constraint("uq_orders_id_customer_id", "orders", type_="unique")
