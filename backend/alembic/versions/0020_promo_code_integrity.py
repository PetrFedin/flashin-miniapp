"""enforce promo code configuration integrity

Revision ID: 0020_promo_code_integrity
Revises: 0019_numeric_money_storage
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa

revision = "0020_promo_code_integrity"
down_revision = "0019_numeric_money_storage"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        sa.text(
            """
            WITH normalized AS (
                SELECT
                    id,
                    upper(trim(coalesce(code, ''))) AS normalized_code,
                    row_number() OVER (
                        PARTITION BY upper(trim(coalesce(code, '')))
                        ORDER BY id
                    ) AS duplicate_rank
                FROM promo_codes
            )
            UPDATE promo_codes AS promo
            SET code = CASE
                WHEN normalized.normalized_code = '' OR normalized.duplicate_rank > 1
                    THEN '__MIGRATED_PROMO_' || promo.id::text || '_' || substr(md5(promo.id::text), 1, 8)
                ELSE normalized.normalized_code
            END
            FROM normalized
            WHERE promo.id = normalized.id
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE promo_codes
            SET discount_type = CASE
                    WHEN lower(trim(coalesce(discount_type, ''))) IN ('percent', 'fixed')
                        THEN lower(trim(discount_type))
                    ELSE 'fixed'
                END,
                discount_value = CASE
                    WHEN discount_value IS NULL OR discount_value <= 0 THEN 0.0001
                    WHEN lower(trim(coalesce(discount_type, ''))) = 'percent' AND discount_value > 100 THEN 100
                    ELSE discount_value
                END,
                min_amount = greatest(coalesce(min_amount, 0), 0),
                max_uses = greatest(coalesce(max_uses, 0), 0),
                used_count = greatest(coalesce(used_count, 0), 0)
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE promo_codes
            SET max_uses = used_count
            WHERE max_uses > 0 AND used_count > max_uses
            """
        )
    )

    op.create_check_constraint(
        "ck_promo_codes_code_nonempty",
        "promo_codes",
        "length(trim(code)) > 0",
    )
    op.create_check_constraint(
        "ck_promo_codes_discount_type_valid",
        "promo_codes",
        "discount_type IN ('percent', 'fixed')",
    )
    op.create_check_constraint(
        "ck_promo_codes_discount_positive",
        "promo_codes",
        "discount_value > 0",
    )
    op.create_check_constraint(
        "ck_promo_codes_percent_within_100",
        "promo_codes",
        "discount_type <> 'percent' OR discount_value <= 100",
    )


def downgrade():
    op.drop_constraint("ck_promo_codes_percent_within_100", "promo_codes", type_="check")
    op.drop_constraint("ck_promo_codes_discount_positive", "promo_codes", type_="check")
    op.drop_constraint("ck_promo_codes_discount_type_valid", "promo_codes", type_="check")
    op.drop_constraint("ck_promo_codes_code_nonempty", "promo_codes", type_="check")
