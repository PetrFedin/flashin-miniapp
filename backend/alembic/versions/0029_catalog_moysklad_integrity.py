"""enforce catalog and MoySklad integrity

Revision ID: 0029_catalog_moysklad_integrity
Revises: 0028_notification_delivery_integrity
Create Date: 2026-07-31
"""

from alembic import op
import sqlalchemy as sa

revision = "0029_catalog_moysklad_integrity"
down_revision = "0028_notification_delivery_integrity"
branch_labels = None
depends_on = None

_MAX_PRICE = "1000000000.00"
_MAX_SYNC_ERROR = 2000


def upgrade():
    # Resolve values that would collide after whitespace normalization before
    # applying trim. Quarantined identities remain unique and visible.
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY trim(coalesce(sku, ''))
                           ORDER BY id
                       ) AS rn
                FROM products
                WHERE trim(coalesce(sku, '')) <> ''
            )
            UPDATE products p
            SET sku = 'LEGACY-PRODUCT-' || p.id::text,
                active = false
            FROM ranked r
            WHERE p.id = r.id AND r.rn > 1
            """
        )
    )
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY trim(coalesce(slug, ''))
                           ORDER BY id
                       ) AS rn
                FROM products
                WHERE trim(coalesce(slug, '')) <> ''
            )
            UPDATE products p
            SET slug = 'legacy-product-' || p.id::text,
                active = false
            FROM ranked r
            WHERE p.id = r.id AND r.rn > 1
            """
        )
    )
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY trim(coalesce(sku, ''))
                           ORDER BY id
                       ) AS rn
                FROM product_variants
                WHERE trim(coalesce(sku, '')) <> ''
            )
            UPDATE product_variants v
            SET sku = 'LEGACY-VARIANT-' || v.id::text
            FROM ranked r
            WHERE v.id = r.id AND r.rn > 1
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE products
            SET sku = CASE
                    WHEN trim(coalesce(sku, '')) = '' THEN 'LEGACY-PRODUCT-' || id::text
                    ELSE left(trim(sku), 120)
                END,
                moysklad_id = left(trim(coalesce(moysklad_id, '')), 255),
                title = CASE
                    WHEN trim(coalesce(title, '')) = '' THEN 'Legacy product #' || id::text
                    ELSE left(trim(title), 255)
                END,
                slug = CASE
                    WHEN trim(coalesce(slug, '')) = '' THEN 'legacy-product-' || id::text
                    ELSE left(trim(slug), 255)
                END,
                brand = CASE
                    WHEN trim(coalesce(brand, '')) = '' THEN 'FLASHIN'
                    ELSE left(trim(brand), 120)
                END,
                category = CASE
                    WHEN trim(coalesce(category, '')) = '' THEN 'Clothing'
                    ELSE left(trim(category), 120)
                END,
                gender = CASE
                    WHEN trim(coalesce(gender, '')) = '' THEN 'unisex'
                    ELSE left(trim(gender), 32)
                END,
                currency = CASE
                    WHEN upper(trim(coalesce(currency, ''))) ~ '^[A-Z]{3}$'
                        THEN upper(trim(currency))
                    ELSE 'RUB'
                END,
                price = least(greatest(coalesce(price, 0), 0), 1000000000.00),
                active = CASE WHEN coalesce(price, 0) > 0 THEN active ELSE false END,
                old_price = CASE
                    WHEN old_price IS NULL THEN NULL
                    WHEN old_price > greatest(coalesce(price, 0), 0)
                         AND old_price <= 1000000000.00
                        THEN old_price
                    ELSE NULL
                END
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE product_variants
            SET sku = CASE
                    WHEN trim(coalesce(sku, '')) = '' THEN 'LEGACY-VARIANT-' || id::text
                    ELSE left(trim(sku), 120)
                END,
                moysklad_id = left(trim(coalesce(moysklad_id, '')), 255),
                size = CASE
                    WHEN trim(coalesce(size, '')) = '' THEN 'ONE SIZE'
                    ELSE left(trim(size), 32)
                END,
                color = left(trim(coalesce(color, '')), 64)
            """
        )
    )

    # A provider identity may map to only one local entity. Preserve the first
    # link and quarantine later duplicates by clearing the provider id.
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY moysklad_id
                           ORDER BY id
                       ) AS rn
                FROM products
                WHERE moysklad_id <> ''
            )
            UPDATE products p
            SET moysklad_id = '', active = false
            FROM ranked r
            WHERE p.id = r.id AND r.rn > 1
            """
        )
    )
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY moysklad_id
                           ORDER BY id
                       ) AS rn
                FROM product_variants
                WHERE moysklad_id <> ''
            )
            UPDATE product_variants v
            SET moysklad_id = ''
            FROM ranked r
            WHERE v.id = r.id AND r.rn > 1
            """
        )
    )

    op.execute(
        sa.text(
            f"""
            UPDATE moysklad_sync_logs
            SET sync_type = CASE
                    WHEN lower(trim(coalesce(sync_type, ''))) IN ('manual', 'scheduled')
                        THEN lower(trim(sync_type))
                    ELSE 'manual'
                END,
                status = lower(trim(coalesce(status, ''))),
                products_seen = greatest(coalesce(products_seen, 0), 0),
                products_upserted = greatest(coalesce(products_upserted, 0), 0),
                variants_upserted = greatest(coalesce(variants_upserted, 0), 0),
                error = left(trim(coalesce(error, '')), {_MAX_SYNC_ERROR})
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE moysklad_sync_logs
            SET status = 'failed',
                finished_at = coalesce(finished_at, CURRENT_TIMESTAMP),
                error = 'Quarantined unknown legacy MoySklad sync status'
            WHERE status NOT IN ('started', 'success', 'failed')
            """
        )
    )
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (
                           ORDER BY created_at DESC NULLS LAST, id DESC
                       ) AS rn
                FROM moysklad_sync_logs
                WHERE status = 'started'
            )
            UPDATE moysklad_sync_logs l
            SET status = 'failed',
                finished_at = CURRENT_TIMESTAMP,
                error = 'Superseded duplicate legacy running sync'
            FROM ranked r
            WHERE l.id = r.id AND r.rn > 1
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE moysklad_sync_logs
            SET finished_at = NULL, error = ''
            WHERE status = 'started'
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE moysklad_sync_logs
            SET finished_at = coalesce(finished_at, CURRENT_TIMESTAMP), error = ''
            WHERE status = 'success'
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE moysklad_sync_logs
            SET finished_at = coalesce(finished_at, CURRENT_TIMESTAMP),
                error = CASE
                    WHEN error = '' THEN 'Legacy MoySklad sync failed'
                    ELSE error
                END
            WHERE status = 'failed'
            """
        )
    )

    for column, maximum in (
        ("sku", 120),
        ("title", 255),
        ("slug", 255),
        ("brand", 120),
        ("category", 120),
        ("gender", 32),
    ):
        op.create_check_constraint(
            f"ck_products_{column}_normalized",
            "products",
            f"{column} = trim({column})",
        )
        op.create_check_constraint(
            f"ck_products_{column}_size",
            "products",
            f"length({column}) BETWEEN 1 AND {maximum}",
        )
    op.create_check_constraint(
        "ck_products_moysklad_id_normalized",
        "products",
        "moysklad_id = trim(moysklad_id)",
    )
    op.create_check_constraint(
        "ck_products_moysklad_id_size",
        "products",
        "length(moysklad_id) <= 255",
    )
    op.create_check_constraint(
        "ck_products_price_range",
        "products",
        f"price BETWEEN 0 AND {_MAX_PRICE}",
    )
    op.create_check_constraint(
        "ck_products_active_price_positive",
        "products",
        "NOT active OR price > 0",
    )
    op.create_check_constraint(
        "ck_products_old_price_coherent",
        "products",
        f"old_price IS NULL OR (old_price > price AND old_price <= {_MAX_PRICE})",
    )
    op.create_check_constraint(
        "ck_products_currency_normalized",
        "products",
        "currency = upper(trim(currency))",
    )
    op.create_check_constraint(
        "ck_products_currency_size",
        "products",
        "length(currency) = 3",
    )

    for column, maximum in (("sku", 120), ("size", 32)):
        op.create_check_constraint(
            f"ck_product_variants_{column}_normalized",
            "product_variants",
            f"{column} = trim({column})",
        )
        op.create_check_constraint(
            f"ck_product_variants_{column}_size",
            "product_variants",
            f"length({column}) BETWEEN 1 AND {maximum}",
        )
    op.create_check_constraint(
        "ck_product_variants_color_normalized",
        "product_variants",
        "color = trim(color)",
    )
    op.create_check_constraint(
        "ck_product_variants_color_size",
        "product_variants",
        "length(color) <= 64",
    )
    op.create_check_constraint(
        "ck_product_variants_moysklad_id_normalized",
        "product_variants",
        "moysklad_id = trim(moysklad_id)",
    )
    op.create_check_constraint(
        "ck_product_variants_moysklad_id_size",
        "product_variants",
        "length(moysklad_id) <= 255",
    )

    op.create_index(
        "uq_products_moysklad_id",
        "products",
        ["moysklad_id"],
        unique=True,
        postgresql_where=sa.text("moysklad_id <> ''"),
    )
    op.create_index(
        "uq_product_variants_moysklad_id",
        "product_variants",
        ["moysklad_id"],
        unique=True,
        postgresql_where=sa.text("moysklad_id <> ''"),
    )

    op.create_check_constraint(
        "ck_moysklad_sync_logs_type_valid",
        "moysklad_sync_logs",
        "sync_type IN ('manual', 'scheduled')",
    )
    op.create_check_constraint(
        "ck_moysklad_sync_logs_status_valid",
        "moysklad_sync_logs",
        "status IN ('started', 'success', 'failed')",
    )
    op.create_check_constraint(
        "ck_moysklad_sync_logs_counts_nonnegative",
        "moysklad_sync_logs",
        "products_seen >= 0 AND products_upserted >= 0 AND variants_upserted >= 0",
    )
    op.create_check_constraint(
        "ck_moysklad_sync_logs_error_size",
        "moysklad_sync_logs",
        f"length(error) <= {_MAX_SYNC_ERROR}",
    )
    op.create_check_constraint(
        "ck_moysklad_sync_logs_state_coherent",
        "moysklad_sync_logs",
        "((status = 'started' AND finished_at IS NULL AND error = '') "
        "OR (status = 'success' AND finished_at IS NOT NULL AND error = '') "
        "OR (status = 'failed' AND finished_at IS NOT NULL AND length(trim(error)) > 0))",
    )
    op.create_index(
        "uq_moysklad_sync_logs_single_started",
        "moysklad_sync_logs",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'started'"),
    )


def downgrade():
    op.drop_index("uq_moysklad_sync_logs_single_started", table_name="moysklad_sync_logs")
    for name in (
        "ck_moysklad_sync_logs_state_coherent",
        "ck_moysklad_sync_logs_error_size",
        "ck_moysklad_sync_logs_counts_nonnegative",
        "ck_moysklad_sync_logs_status_valid",
        "ck_moysklad_sync_logs_type_valid",
    ):
        op.drop_constraint(name, "moysklad_sync_logs", type_="check")

    op.drop_index("uq_product_variants_moysklad_id", table_name="product_variants")
    op.drop_index("uq_products_moysklad_id", table_name="products")

    for name in (
        "ck_product_variants_moysklad_id_size",
        "ck_product_variants_moysklad_id_normalized",
        "ck_product_variants_color_size",
        "ck_product_variants_color_normalized",
        "ck_product_variants_size_size",
        "ck_product_variants_size_normalized",
        "ck_product_variants_sku_size",
        "ck_product_variants_sku_normalized",
    ):
        op.drop_constraint(name, "product_variants", type_="check")

    for name in (
        "ck_products_currency_size",
        "ck_products_currency_normalized",
        "ck_products_old_price_coherent",
        "ck_products_active_price_positive",
        "ck_products_price_range",
        "ck_products_moysklad_id_size",
        "ck_products_moysklad_id_normalized",
    ):
        op.drop_constraint(name, "products", type_="check")
    for column in ("gender", "category", "brand", "slug", "title", "sku"):
        op.drop_constraint(f"ck_products_{column}_size", "products", type_="check")
        op.drop_constraint(f"ck_products_{column}_normalized", "products", type_="check")
