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
    # Build the complete final mapping before touching the unique code column.
    # The lowercase temporary namespace cannot collide with final normalized
    # codes because every final code is uppercase.
    op.execute(
        sa.text(
            """
            CREATE TEMP TABLE promo_code_normalization_map
            ON COMMIT DROP
            AS
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
            SELECT
                id,
                normalized_code,
                duplicate_rank,
                NULL::varchar(64) AS final_code
            FROM normalized
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE promo_codes
            SET code = '__promo_tmp_' || id::text || '_' || substr(md5(id::text), 1, 12)
            """
        )
    )
    op.execute(
        sa.text(
            """
            DO $$
            DECLARE
                promo_row record;
                base_candidate text;
                candidate text;
                collision_counter integer;
            BEGIN
                FOR promo_row IN
                    SELECT id, normalized_code, duplicate_rank
                    FROM promo_code_normalization_map
                    ORDER BY id
                LOOP
                    IF promo_row.normalized_code <> '' AND promo_row.duplicate_rank = 1 THEN
                        base_candidate := promo_row.normalized_code;
                    ELSE
                        base_candidate := '__MIGRATED_PROMO_' || promo_row.id::text || '_'
                            || substr(md5(promo_row.id::text || promo_row.normalized_code), 1, 8);
                    END IF;

                    candidate := left(base_candidate, 64);
                    collision_counter := 0;
                    WHILE EXISTS (
                        SELECT 1
                        FROM promo_code_normalization_map
                        WHERE final_code = candidate
                    ) LOOP
                        collision_counter := collision_counter + 1;
                        candidate := left(base_candidate, 54) || '_'
                            || substr(md5(base_candidate || collision_counter::text), 1, 8);
                    END LOOP;

                    UPDATE promo_code_normalization_map
                    SET final_code = candidate
                    WHERE id = promo_row.id;
                END LOOP;
            END $$
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE promo_codes AS promo
            SET code = mapping.final_code
            FROM promo_code_normalization_map AS mapping
            WHERE promo.id = mapping.id
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
        "ck_promo_codes_code_normalized",
        "promo_codes",
        "code = upper(trim(code))",
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
    op.drop_constraint("ck_promo_codes_code_normalized", "promo_codes", type_="check")
    op.drop_constraint("ck_promo_codes_code_nonempty", "promo_codes", type_="check")
