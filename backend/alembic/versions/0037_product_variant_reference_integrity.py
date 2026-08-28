"""Enforce product/variant reference integrity for cart and order items.

Revision ID: 0037_product_variant_reference_integrity
Revises: 0036_customer_owned_reference_integrity
"""

from collections.abc import Iterable

from alembic import op
import sqlalchemy as sa


revision = "0037_product_variant_reference_integrity"
down_revision = "0036_customer_owned_reference_integrity"
branch_labels = None
depends_on = None


ReferenceCheck = tuple[str, str]


REFERENCE_CHECKS: tuple[ReferenceCheck, ...] = (
    (
        "cart_items.variant_id/product_id",
        """
        SELECT count(*)
        FROM cart_items AS child
        JOIN product_variants AS parent ON parent.id = child.variant_id
        WHERE child.product_id <> parent.product_id
        """,
    ),
    (
        "order_items.variant_id/product_id",
        """
        SELECT count(*)
        FROM order_items AS child
        JOIN product_variants AS parent ON parent.id = child.variant_id
        WHERE child.product_id <> parent.product_id
        """,
    ),
)


def _find_reference_mismatches(
    bind,
    checks: Iterable[ReferenceCheck] = REFERENCE_CHECKS,
) -> list[str]:
    failures: list[str] = []
    for label, query in checks:
        count = int(bind.execute(sa.text(query)).scalar_one())
        if count:
            failures.append(f"{label}={count}")
    return failures


def _assert_product_variant_references_are_clean(bind) -> None:
    failures = _find_reference_mismatches(bind)
    if failures:
        joined = ", ".join(failures)
        raise RuntimeError(
            "Product/variant reference integrity preflight failed; "
            "migration will not rewrite or delete existing cart/order records: "
            f"{joined}"
        )


def upgrade() -> None:
    bind = op.get_bind()
    _assert_product_variant_references_are_clean(bind)

    op.create_unique_constraint(
        "uq_product_variants_id_product_id",
        "product_variants",
        ["id", "product_id"],
    )
    op.create_foreign_key(
        "fk_cart_items_variant_product",
        "cart_items",
        "product_variants",
        ["variant_id", "product_id"],
        ["id", "product_id"],
    )
    op.create_foreign_key(
        "fk_order_items_variant_product",
        "order_items",
        "product_variants",
        ["variant_id", "product_id"],
        ["id", "product_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_order_items_variant_product",
        "order_items",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_cart_items_variant_product",
        "cart_items",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_product_variants_id_product_id",
        "product_variants",
        type_="unique",
    )
