"""Enforce immutable MoySklad product/variant provider identity.

Revision ID: 0043_moysklad_variant_identity_authority
Revises: 0042_moysklad_stock_evidence_concurrency
Create Date: 2026-09-19
"""

from alembic import op
import sqlalchemy as sa


revision = "0043_moysklad_variant_identity_authority"
down_revision = "0042_moysklad_stock_evidence_concurrency"
branch_labels = None
depends_on = None

_IDENTITY_TABLES = (
    ("products", "product"),
    ("product_variants", "variant"),
)


def _duplicate_provider_identity(bind, table_name: str):
    return bind.execute(
        sa.text(
            f"""
            SELECT moysklad_id, count(*) AS duplicate_count
            FROM {table_name}
            WHERE moysklad_id IS NOT NULL
              AND moysklad_id <> ''
            GROUP BY moysklad_id
            HAVING count(*) > 1
            ORDER BY moysklad_id
            LIMIT 1
            """
        )
    ).first()


def _assert_unique_provider_id_data(bind) -> None:
    """Reject ambiguous legacy mappings without repairing or deleting data."""

    for table_name, entity_name in _IDENTITY_TABLES:
        duplicate = _duplicate_provider_identity(bind, table_name)
        if duplicate is None:
            continue
        provider_id = str(duplicate[0])
        duplicate_count = int(duplicate[1])
        raise RuntimeError(
            "0043 blocked: duplicate non-empty MoySklad "
            f"{entity_name} identity {provider_id!r} appears {duplicate_count} times in {table_name}; "
            "resolve identity evidence explicitly before migration"
        )


def upgrade() -> None:
    bind = op.get_bind()
    _assert_unique_provider_id_data(bind)

    product_predicate = sa.text("moysklad_id <> ''")
    variant_predicate = sa.text("moysklad_id <> ''")
    op.create_index(
        "uq_products_moysklad_id_nonempty",
        "products",
        ["moysklad_id"],
        unique=True,
        postgresql_where=product_predicate,
        sqlite_where=product_predicate,
    )
    op.create_index(
        "uq_product_variants_moysklad_id_nonempty",
        "product_variants",
        ["moysklad_id"],
        unique=True,
        postgresql_where=variant_predicate,
        sqlite_where=variant_predicate,
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
