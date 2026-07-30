"""enforce provider payment record integrity

Revision ID: 0024_payment_record_integrity
Revises: 0023_order_state_integrity
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa

revision = "0024_payment_record_integrity"
down_revision = "0023_order_state_integrity"
branch_labels = None
depends_on = None

_VALID_STATUSES = ("pending", "waiting_for_capture", "succeeded", "canceled")


def _sql_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade():
    valid_statuses = _sql_values(_VALID_STATUSES)

    op.execute(
        sa.text(
            f"""
            CREATE TEMP TABLE payment_record_normalization_map
            ON COMMIT DROP
            AS
            WITH normalized AS (
                SELECT
                    id,
                    CASE
                        WHEN length(trim(coalesce(provider, ''))) = 0 THEN 'legacy_unresolved'
                        ELSE lower(trim(provider))
                    END AS normalized_provider,
                    trim(coalesce(provider_payment_id, '')) AS normalized_payment_id,
                    CASE
                        WHEN lower(trim(coalesce(status, ''))) = 'paid' THEN 'succeeded'
                        WHEN lower(trim(coalesce(status, ''))) IN ({valid_statuses})
                            THEN lower(trim(status))
                        ELSE 'canceled'
                    END AS normalized_status,
                    trim(coalesce(confirmation_url, '')) AS normalized_confirmation_url
                FROM payments
            ), ranked AS (
                SELECT
                    *,
                    row_number() OVER (
                        PARTITION BY normalized_provider, normalized_payment_id
                        ORDER BY id
                    ) AS duplicate_rank
                FROM normalized
            )
            SELECT
                id,
                normalized_provider,
                normalized_payment_id,
                normalized_status,
                normalized_confirmation_url,
                duplicate_rank,
                NULL::varchar(64) AS final_provider,
                NULL::varchar(255) AS final_payment_id,
                NULL::varchar(64) AS final_status,
                NULL::varchar(2048) AS final_confirmation_url
            FROM ranked
            """
        )
    )

    # Move every row into a unique temporary namespace before provider
    # normalization can collapse case/space variants under the existing index.
    op.execute(
        sa.text(
            """
            UPDATE payments
            SET provider = 'migration_tmp',
                provider_payment_id = '__payment_tmp_' || id::text || '_'
                    || substr(md5(id::text), 1, 12)
            """
        )
    )

    op.execute(
        sa.text(
            """
            DO $$
            DECLARE
                payment_row record;
                candidate_provider text;
                candidate_id text;
                collision_counter integer;
            BEGIN
                FOR payment_row IN
                    SELECT *
                    FROM payment_record_normalization_map
                    ORDER BY id
                LOOP
                    IF payment_row.normalized_payment_id <> ''
                       AND payment_row.duplicate_rank = 1
                       AND payment_row.normalized_provider <> 'legacy_unresolved' THEN
                        candidate_provider := payment_row.normalized_provider;
                        candidate_id := payment_row.normalized_payment_id;
                    ELSE
                        candidate_provider := 'legacy_unresolved';
                        candidate_id := 'legacy-unresolved-' || payment_row.id::text || '-'
                            || substr(md5(
                                payment_row.id::text
                                || payment_row.normalized_provider
                                || payment_row.normalized_payment_id
                            ), 1, 12);
                    END IF;

                    collision_counter := 0;
                    WHILE EXISTS (
                        SELECT 1
                        FROM payment_record_normalization_map
                        WHERE final_provider = candidate_provider
                          AND final_payment_id = candidate_id
                    ) LOOP
                        collision_counter := collision_counter + 1;
                        candidate_id := left(candidate_id, 242) || '-'
                            || substr(md5(candidate_id || collision_counter::text), 1, 12);
                    END LOOP;

                    UPDATE payment_record_normalization_map
                    SET final_provider = candidate_provider,
                        final_payment_id = candidate_id,
                        final_status = CASE
                            WHEN candidate_provider = 'legacy_unresolved' THEN 'canceled'
                            ELSE payment_row.normalized_status
                        END,
                        final_confirmation_url = CASE
                            WHEN candidate_provider = 'legacy_unresolved' THEN ''
                            ELSE payment_row.normalized_confirmation_url
                        END
                    WHERE id = payment_row.id;
                END LOOP;
            END $$
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE payments AS payment
            SET provider = mapping.final_provider,
                provider_payment_id = mapping.final_payment_id,
                status = mapping.final_status,
                confirmation_url = mapping.final_confirmation_url
            FROM payment_record_normalization_map AS mapping
            WHERE payment.id = mapping.id
            """
        )
    )

    op.create_check_constraint(
        "ck_payments_provider_normalized",
        "payments",
        "length(trim(provider)) > 0 AND provider = lower(trim(provider))",
    )
    op.create_check_constraint(
        "ck_payments_provider_payment_id_normalized",
        "payments",
        "length(trim(provider_payment_id)) > 0 AND provider_payment_id = trim(provider_payment_id)",
    )
    op.create_check_constraint(
        "ck_payments_status_valid",
        "payments",
        f"status IN ({valid_statuses})",
    )
    op.create_check_constraint(
        "ck_payments_confirmation_url_normalized",
        "payments",
        "confirmation_url = trim(confirmation_url)",
    )


def downgrade():
    op.drop_constraint("ck_payments_confirmation_url_normalized", "payments", type_="check")
    op.drop_constraint("ck_payments_status_valid", "payments", type_="check")
    op.drop_constraint("ck_payments_provider_payment_id_normalized", "payments", type_="check")
    op.drop_constraint("ck_payments_provider_normalized", "payments", type_="check")
