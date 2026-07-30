"""enforce provider payment event integrity

Revision ID: 0025_payment_event_integrity
Revises: 0024_payment_record_integrity
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa

revision = "0025_payment_event_integrity"
down_revision = "0024_payment_record_integrity"
branch_labels = None
depends_on = None

_ACTIONABLE_EVENT_TYPES = (
    "payment.waiting_for_capture",
    "payment.succeeded",
    "payment.canceled",
)
_PERSISTED_EVENT_TYPES = (*_ACTIONABLE_EVENT_TYPES, "payment.unresolved")


def _sql_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade():
    actionable_events = _sql_values(_ACTIONABLE_EVENT_TYPES)
    persisted_events = _sql_values(_PERSISTED_EVENT_TYPES)

    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION pg_temp.is_json_object(value text)
            RETURNS boolean
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RETURN jsonb_typeof(value::jsonb) = 'object';
            EXCEPTION WHEN others THEN
                RETURN false;
            END;
            $$
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE TEMP TABLE payment_event_normalization_map
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
                    lower(trim(coalesce(event_type, ''))) AS normalized_event_type,
                    trim(coalesce(raw_payload, '')) AS normalized_payload,
                    processed,
                    pg_temp.is_json_object(trim(coalesce(raw_payload, ''))) AS payload_is_object
                FROM payment_events
            ), ranked AS (
                SELECT
                    *,
                    row_number() OVER (
                        PARTITION BY normalized_provider, normalized_payment_id, normalized_event_type
                        ORDER BY id
                    ) AS duplicate_rank
                FROM normalized
            )
            SELECT
                *,
                NULL::varchar(64) AS final_provider,
                NULL::varchar(255) AS final_payment_id,
                NULL::varchar(120) AS final_event_type,
                NULL::text AS final_payload,
                NULL::boolean AS final_processed
            FROM ranked
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE payment_events
            SET provider = 'migration_tmp',
                provider_payment_id = '__event_tmp_' || id::text || '_'
                    || substr(md5(id::text), 1, 12),
                event_type = 'payment.unresolved'
            """
        )
    )

    op.execute(
        sa.text(
            f"""
            DO $$
            DECLARE
                event_row record;
                candidate_provider text;
                candidate_id text;
                candidate_event_type text;
                collision_counter integer;
                is_valid boolean;
            BEGIN
                FOR event_row IN
                    SELECT *
                    FROM payment_event_normalization_map
                    ORDER BY id
                LOOP
                    is_valid := event_row.normalized_provider <> 'legacy_unresolved'
                        AND event_row.normalized_payment_id <> ''
                        AND event_row.normalized_event_type IN ({actionable_events})
                        AND event_row.payload_is_object
                        AND event_row.duplicate_rank = 1;

                    IF is_valid THEN
                        candidate_provider := event_row.normalized_provider;
                        candidate_id := event_row.normalized_payment_id;
                        candidate_event_type := event_row.normalized_event_type;
                    ELSE
                        candidate_provider := 'legacy_unresolved';
                        candidate_id := 'legacy-event-' || event_row.id::text || '-'
                            || substr(md5(
                                event_row.id::text
                                || event_row.normalized_provider
                                || event_row.normalized_payment_id
                                || event_row.normalized_event_type
                            ), 1, 12);
                        candidate_event_type := 'payment.unresolved';
                    END IF;

                    collision_counter := 0;
                    WHILE EXISTS (
                        SELECT 1
                        FROM payment_event_normalization_map
                        WHERE final_provider = candidate_provider
                          AND final_payment_id = candidate_id
                          AND final_event_type = candidate_event_type
                    ) LOOP
                        collision_counter := collision_counter + 1;
                        candidate_id := left(candidate_id, 242) || '-'
                            || substr(md5(candidate_id || collision_counter::text), 1, 12);
                    END LOOP;

                    UPDATE payment_event_normalization_map
                    SET final_provider = candidate_provider,
                        final_payment_id = candidate_id,
                        final_event_type = candidate_event_type,
                        final_payload = CASE
                            WHEN is_valid THEN event_row.normalized_payload::jsonb::text
                            ELSE jsonb_build_object(
                                'legacy_event_id', event_row.id,
                                'original_provider', event_row.normalized_provider,
                                'original_payment_id', event_row.normalized_payment_id,
                                'original_event_type', event_row.normalized_event_type,
                                'original_payload', event_row.normalized_payload
                            )::text
                        END,
                        final_processed = CASE WHEN is_valid THEN event_row.processed ELSE false END
                    WHERE id = event_row.id;
                END LOOP;
            END $$
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE payment_events AS event
            SET provider = mapping.final_provider,
                provider_payment_id = mapping.final_payment_id,
                event_type = mapping.final_event_type,
                raw_payload = mapping.final_payload,
                processed = mapping.final_processed
            FROM payment_event_normalization_map AS mapping
            WHERE event.id = mapping.id
            """
        )
    )

    op.create_check_constraint(
        "ck_payment_events_provider_normalized",
        "payment_events",
        "length(trim(provider)) > 0 AND provider = lower(trim(provider))",
    )
    op.create_check_constraint(
        "ck_payment_events_provider_payment_id_normalized",
        "payment_events",
        "length(trim(provider_payment_id)) > 0 AND provider_payment_id = trim(provider_payment_id)",
    )
    op.create_check_constraint(
        "ck_payment_events_event_type_valid",
        "payment_events",
        f"event_type IN ({persisted_events})",
    )
    op.create_check_constraint(
        "ck_payment_events_event_type_normalized",
        "payment_events",
        "event_type = lower(trim(event_type))",
    )
    op.create_check_constraint(
        "ck_payment_events_payload_nonempty",
        "payment_events",
        "length(trim(raw_payload)) > 0",
    )
    op.create_check_constraint(
        "ck_payment_events_legacy_state_coherent",
        "payment_events",
        """
        (provider = 'legacy_unresolved' AND event_type = 'payment.unresolved' AND processed = false)
        OR (provider <> 'legacy_unresolved' AND event_type <> 'payment.unresolved')
        """,
    )


def downgrade():
    op.drop_constraint("ck_payment_events_legacy_state_coherent", "payment_events", type_="check")
    op.drop_constraint("ck_payment_events_payload_nonempty", "payment_events", type_="check")
    op.drop_constraint("ck_payment_events_event_type_normalized", "payment_events", type_="check")
    op.drop_constraint("ck_payment_events_event_type_valid", "payment_events", type_="check")
    op.drop_constraint(
        "ck_payment_events_provider_payment_id_normalized",
        "payment_events",
        type_="check",
    )
    op.drop_constraint("ck_payment_events_provider_normalized", "payment_events", type_="check")
