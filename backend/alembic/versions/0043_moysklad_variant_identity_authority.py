"""Make MoySklad product/variant provider identity unique and fail-closed.

Revision ID: 0043_moysklad_variant_identity_authority
Revises: 0042_moysklad_stock_evidence_concurrency
Create Date: 2026-09-18
"""

from alembic import op
import sqlalchemy as sa


revision = "0043_moysklad_variant_identity_authority"
down_revision = "0042_moysklad_stock_evidence_concurrency"
branch_labels = None
depends_on = None


IDENTITY_CHECKS: tuple[tuple[str, str], ...] = (
    (
        "products.moysklad_id",
        """
        SELECT count(*)
        FROM (
            SELECT moysklad_id
            FROM products
            WHERE trim(moysklad_id) <> ''
            GROUP BY moysklad_id
            HAVING count(*) > 1
        ) AS duplicate_product_identity
        """,
    ),
    (
        "product_variants.moysklad_id",
        """
        SELECT count(*)
        FROM (
            SELECT moysklad_id
            FROM product_variants
            WHERE trim(moysklad_id) <> ''
            GROUP BY moysklad_id
            HAVING count(*) > 1
        ) AS duplicate_variant_identity
        """,
    ),
)


def _identity_conflicts(bind) -> list[str]:
    conflicts: list[str] = []
    for label, query in IDENTITY_CHECKS:
        count = int(bind.execute(sa.text(query)).scalar_one())
        if count:
            conflicts.append(f"{label} duplicate_groups={count}")
    return conflicts


def _assert_moysklad_identity_clean(bind) -> None:
    conflicts = _identity_conflicts(bind)
    if conflicts:
        raise RuntimeError(
            "MoySklad identity authority preflight failed; migration will not "
            "merge, delete, or rewrite provider mappings: " + ", ".join(conflicts)
        )


def upgrade() -> None:
    _assert_moysklad_identity_clean(op.get_bind())

    nonempty = sa.text("moysklad_id <> ''")
    op.create_index(
        "uq_products_moysklad_id_nonempty",
        "products",
        ["moysklad_id"],
        unique=True,
        postgresql_where=nonempty,
        sqlite_where=nonempty,
    )
    op.create_index(
        "uq_product_variants_moysklad_id_nonempty",
        "product_variants",
        ["moysklad_id"],
        unique=True,
        postgresql_where=nonempty,
        sqlite_where=nonempty,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_product_variants_moysklad_id_nonempty",
        table_name="product_variants",
    )
    op.drop_index(
        "uq_products_moysklad_id_nonempty",
        table_name="products",
    )
