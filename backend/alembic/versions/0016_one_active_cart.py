"""reconcile duplicate active carts

Revision ID: 0016_one_active_cart
Revises: 0015_checkout_idempotency
Create Date: 2026-08-01

The active-cart unique index is owned by revision 0010. This revision repairs
any drifted data and restores that index only when an environment lost it.
"""

from alembic import op
import sqlalchemy as sa

revision = "0016_one_active_cart"
down_revision = "0015_checkout_idempotency"
branch_labels = None
depends_on = None


_ACTIVE_CART_INDEX = "uq_carts_one_active_per_customer"


def upgrade():
    op.execute(
        sa.text(
            """
            CREATE TEMP TABLE active_cart_dedupe_map ON COMMIT DROP AS
            WITH ranked_active_carts AS (
                SELECT
                    id AS cart_id,
                    FIRST_VALUE(id) OVER (
                        PARTITION BY customer_id
                        ORDER BY updated_at DESC NULLS LAST, created_at DESC, id DESC
                    ) AS primary_id,
                    ROW_NUMBER() OVER (
                        PARTITION BY customer_id
                        ORDER BY updated_at DESC NULLS LAST, created_at DESC, id DESC
                    ) AS row_number
                FROM carts
                WHERE status = 'active'
            )
            SELECT cart_id AS duplicate_id, primary_id
            FROM ranked_active_carts
            WHERE row_number > 1
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE carts AS primary_cart
            SET promo_code_id = candidate.promo_code_id
            FROM (
                SELECT DISTINCT ON (mapping.primary_id)
                    mapping.primary_id,
                    duplicate_cart.promo_code_id
                FROM active_cart_dedupe_map AS mapping
                JOIN carts AS duplicate_cart ON duplicate_cart.id = mapping.duplicate_id
                WHERE duplicate_cart.promo_code_id IS NOT NULL
                ORDER BY
                    mapping.primary_id,
                    duplicate_cart.updated_at DESC NULLS LAST,
                    duplicate_cart.created_at DESC,
                    duplicate_cart.id DESC
            ) AS candidate
            WHERE primary_cart.id = candidate.primary_id
              AND primary_cart.promo_code_id IS NULL
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE carts AS primary_cart
            SET referral_code = candidate.referral_code
            FROM (
                SELECT DISTINCT ON (mapping.primary_id)
                    mapping.primary_id,
                    duplicate_cart.referral_code
                FROM active_cart_dedupe_map AS mapping
                JOIN carts AS duplicate_cart ON duplicate_cart.id = mapping.duplicate_id
                WHERE COALESCE(duplicate_cart.referral_code, '') <> ''
                ORDER BY
                    mapping.primary_id,
                    duplicate_cart.updated_at DESC NULLS LAST,
                    duplicate_cart.created_at DESC,
                    duplicate_cart.id DESC
            ) AS candidate
            WHERE primary_cart.id = candidate.primary_id
              AND COALESCE(primary_cart.referral_code, '') = ''
            """
        )
    )

    op.execute(
        sa.text(
            """
            INSERT INTO cart_items (
                cart_id,
                product_id,
                variant_id,
                quantity,
                created_at
            )
            SELECT
                mapping.primary_id,
                variant.product_id,
                item.variant_id,
                LEAST(SUM(item.quantity), 10)::integer,
                MIN(item.created_at)
            FROM active_cart_dedupe_map AS mapping
            JOIN cart_items AS item ON item.cart_id = mapping.duplicate_id
            JOIN product_variants AS variant ON variant.id = item.variant_id
            GROUP BY mapping.primary_id, variant.product_id, item.variant_id
            ON CONFLICT (cart_id, variant_id)
            DO UPDATE SET
                product_id = EXCLUDED.product_id,
                quantity = LEAST(cart_items.quantity + EXCLUDED.quantity, 10)
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE loyalty_redemption_holds
            SET
                status = 'released',
                released_at = COALESCE(
                    released_at,
                    CURRENT_TIMESTAMP AT TIME ZONE 'UTC'
                )
            WHERE status = 'reserved'
              AND cart_id IN (
                  SELECT duplicate_id
                  FROM active_cart_dedupe_map
              )
            """
        )
    )

    op.execute(
        sa.text(
            """
            DELETE FROM cart_items
            WHERE cart_id IN (
                SELECT duplicate_id
                FROM active_cart_dedupe_map
            )
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE carts
            SET
                status = 'superseded',
                loyalty_points_to_redeem = 0
            WHERE id IN (
                SELECT duplicate_id
                FROM active_cart_dedupe_map
            )
            """
        )
    )

    op.execute(
        sa.text(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS {_ACTIVE_CART_INDEX}
            ON carts (customer_id)
            WHERE status = 'active'
            """
        )
    )


def downgrade():
    # The active-cart index is owned by revision 0010 and must remain in place.
    pass
