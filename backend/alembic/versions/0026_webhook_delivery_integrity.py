"""enforce webhook destination and outbox delivery integrity

Revision ID: 0026_webhook_delivery_integrity
Revises: 0025_payment_event_integrity
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa

revision = "0026_webhook_delivery_integrity"
down_revision = "0025_payment_event_integrity"
branch_labels = None
depends_on = None

_OUTBOX_STATUSES = ("pending", "sent", "failed", "discarded")
_MAX_ATTEMPTS = 10


def _sql_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade():
    outbox_statuses = _sql_values(_OUTBOX_STATUSES)

    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION pg_temp.is_json_collection(value text)
            RETURNS boolean
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RETURN jsonb_typeof(value::jsonb) IN ('object', 'array');
            EXCEPTION WHEN others THEN
                RETURN false;
            END;
            $$
            """
        )
    )

    # Normalize destination identity through a temporary namespace so the
    # existing URL/event unique constraint cannot collide mid-update.
    op.execute(
        sa.text(
            """
            CREATE TEMP TABLE webhook_destination_normalization_map
            ON COMMIT DROP
            AS
            WITH normalized AS (
                SELECT
                    id,
                    trim(coalesce(name, '')) AS normalized_name,
                    trim(coalesce(url, '')) AS normalized_url,
                    lower(trim(coalesce(event_type, '*'))) AS normalized_event_type,
                    trim(coalesce(signing_secret, '')) AS normalized_secret,
                    active
                FROM webhook_destinations
            ), classified AS (
                SELECT
                    *,
                    (
                        length(normalized_url) BETWEEN 1 AND 255
                        AND normalized_url ~* '^https?://'
                        AND position('@' in normalized_url) = 0
                        AND position('#' in normalized_url) = 0
                    ) AS url_is_usable,
                    (
                        normalized_event_type = '*'
                        OR normalized_event_type ~ '^[a-z0-9_.:-]+$'
                    ) AND length(normalized_event_type) BETWEEN 1 AND 120 AS event_is_usable,
                    (
                        normalized_secret = ''
                        OR length(normalized_secret) BETWEEN 32 AND 255
                    ) AS secret_is_usable
                FROM normalized
            ), ranked AS (
                SELECT
                    *,
                    row_number() OVER (
                        PARTITION BY normalized_url, normalized_event_type
                        ORDER BY id
                    ) AS duplicate_rank
                FROM classified
            )
            SELECT
                *,
                NULL::varchar(255) AS final_name,
                NULL::varchar(2048) AS final_url,
                NULL::varchar(120) AS final_event_type,
                NULL::varchar(255) AS final_secret,
                NULL::boolean AS final_active
            FROM ranked
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE webhook_destinations
            SET url = 'https://invalid.invalid/migration/' || id::text,
                event_type = 'legacy.migration.' || id::text
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE webhook_destination_normalization_map
            SET final_name = CASE
                    WHEN normalized_name = '' THEN 'Legacy webhook destination #' || id::text
                    ELSE left(normalized_name, 255)
                END,
                final_url = CASE
                    WHEN url_is_usable AND event_is_usable AND secret_is_usable AND duplicate_rank = 1
                        THEN normalized_url
                    ELSE 'https://invalid.invalid/legacy/' || id::text
                END,
                final_event_type = CASE
                    WHEN url_is_usable AND event_is_usable AND secret_is_usable AND duplicate_rank = 1
                        THEN normalized_event_type
                    ELSE 'legacy.unresolved.' || id::text
                END,
                final_secret = CASE
                    WHEN secret_is_usable THEN normalized_secret
                    ELSE ''
                END,
                final_active = CASE
                    WHEN url_is_usable AND event_is_usable AND secret_is_usable AND duplicate_rank = 1
                        THEN active
                    ELSE false
                END
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE webhook_destinations AS destination
            SET name = mapping.final_name,
                url = mapping.final_url,
                event_type = mapping.final_event_type,
                signing_secret = mapping.final_secret,
                active = mapping.final_active
            FROM webhook_destination_normalization_map AS mapping
            WHERE destination.id = mapping.id
            """
        )
    )

    op.execute(
        sa.text(
            f"""
            CREATE TEMP TABLE webhook_outbox_normalization_map
            ON COMMIT DROP
            AS
            SELECT
                id,
                trim(coalesce(destination, '')) AS normalized_destination,
                lower(trim(coalesce(event_type, ''))) AS normalized_event_type,
                trim(coalesce(payload, '')) AS normalized_payload,
                lower(trim(coalesce(status, ''))) AS normalized_status,
                least(greatest(coalesce(attempts, 0), 0), {_MAX_ATTEMPTS}) AS normalized_attempts,
                left(trim(coalesce(last_error, '')), 2000) AS normalized_error,
                next_attempt_at,
                pg_temp.is_json_collection(trim(coalesce(payload, ''))) AS payload_is_usable,
                (
                    length(trim(coalesce(event_type, ''))) BETWEEN 1 AND 120
                    AND lower(trim(event_type)) ~ '^[a-z0-9_.:-]+$'
                ) AS event_is_usable,
                (
                    length(trim(coalesce(destination, ''))) BETWEEN 1 AND 255
                ) AS destination_is_nonempty,
                (
                    trim(coalesce(destination, '')) ~* '^https?://'
                    AND position('@' in trim(coalesce(destination, ''))) = 0
                    AND position('#' in trim(coalesce(destination, ''))) = 0
                ) AS destination_is_external,
                lower(trim(coalesce(status, ''))) IN ({outbox_statuses}) AS status_is_usable
            FROM webhook_outbox
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            UPDATE webhook_outbox AS row
            SET destination = CASE
                    WHEN mapping.destination_is_nonempty THEN mapping.normalized_destination
                    ELSE 'https://invalid.invalid/outbox/' || row.id::text
                END,
                event_type = CASE
                    WHEN mapping.event_is_usable THEN mapping.normalized_event_type
                    ELSE 'legacy.unresolved.' || row.id::text
                END,
                payload = CASE
                    WHEN mapping.payload_is_usable THEN mapping.normalized_payload::jsonb::text
                    ELSE jsonb_build_object(
                        'legacy_outbox_id', row.id,
                        'original_payload', mapping.normalized_payload
                    )::text
                END,
                status = CASE
                    WHEN NOT mapping.payload_is_usable
                      OR NOT mapping.event_is_usable
                      OR NOT mapping.destination_is_nonempty
                      OR NOT mapping.status_is_usable
                      OR (
                          mapping.normalized_status IN ('pending', 'sent')
                          AND NOT mapping.destination_is_external
                      )
                        THEN 'discarded'
                    WHEN mapping.normalized_status = 'pending'
                         AND mapping.normalized_attempts >= {_MAX_ATTEMPTS}
                        THEN 'failed'
                    ELSE mapping.normalized_status
                END,
                attempts = CASE
                    WHEN mapping.normalized_status = 'failed'
                      OR (
                          mapping.normalized_status = 'pending'
                          AND mapping.normalized_attempts >= {_MAX_ATTEMPTS}
                      )
                        THEN {_MAX_ATTEMPTS}
                    ELSE mapping.normalized_attempts
                END,
                last_error = CASE
                    WHEN NOT mapping.payload_is_usable
                      OR NOT mapping.event_is_usable
                      OR NOT mapping.destination_is_nonempty
                      OR NOT mapping.status_is_usable
                      OR (
                          mapping.normalized_status IN ('pending', 'sent')
                          AND NOT mapping.destination_is_external
                      )
                        THEN coalesce(nullif(mapping.normalized_error, ''), 'Discarded invalid legacy webhook row')
                    WHEN mapping.normalized_status = 'failed'
                      OR (
                          mapping.normalized_status = 'pending'
                          AND mapping.normalized_attempts >= {_MAX_ATTEMPTS}
                      )
                        THEN coalesce(nullif(mapping.normalized_error, ''), 'Webhook attempt limit reached')
                    WHEN mapping.normalized_status = 'discarded'
                        THEN coalesce(nullif(mapping.normalized_error, ''), 'Discarded legacy webhook row')
                    ELSE mapping.normalized_error
                END,
                next_attempt_at = CASE
                    WHEN mapping.payload_is_usable
                     AND mapping.event_is_usable
                     AND mapping.destination_is_nonempty
                     AND mapping.destination_is_external
                     AND mapping.status_is_usable
                     AND mapping.normalized_status = 'pending'
                     AND mapping.normalized_attempts < {_MAX_ATTEMPTS}
                        THEN coalesce(mapping.next_attempt_at, now())
                    ELSE NULL
                END
            FROM webhook_outbox_normalization_map AS mapping
            WHERE row.id = mapping.id
            """
        )
    )

    op.create_check_constraint(
        "ck_webhook_destinations_name_nonempty",
        "webhook_destinations",
        "length(trim(name)) > 0",
    )
    op.create_check_constraint(
        "ck_webhook_destinations_name_normalized",
        "webhook_destinations",
        "name = trim(name)",
    )
    op.create_check_constraint(
        "ck_webhook_destinations_url_normalized",
        "webhook_destinations",
        "url = trim(url)",
    )
    op.create_check_constraint(
        "ck_webhook_destinations_event_type_normalized",
        "webhook_destinations",
        "event_type = lower(trim(event_type))",
    )
    op.create_check_constraint(
        "ck_webhook_destinations_secret_length",
        "webhook_destinations",
        "length(signing_secret) = 0 OR length(signing_secret) BETWEEN 32 AND 255",
    )

    op.create_check_constraint(
        "ck_webhook_outbox_destination_nonempty",
        "webhook_outbox",
        "length(trim(destination)) > 0",
    )
    op.create_check_constraint(
        "ck_webhook_outbox_event_type_normalized",
        "webhook_outbox",
        "length(trim(event_type)) > 0 AND event_type = lower(trim(event_type))",
    )
    op.create_check_constraint(
        "ck_webhook_outbox_payload_nonempty",
        "webhook_outbox",
        "length(trim(payload)) > 0",
    )
    op.create_check_constraint(
        "ck_webhook_outbox_status_valid",
        "webhook_outbox",
        f"status IN ({outbox_statuses})",
    )
    op.create_check_constraint(
        "ck_webhook_outbox_attempts_bounded",
        "webhook_outbox",
        f"attempts BETWEEN 0 AND {_MAX_ATTEMPTS}",
    )
    op.create_check_constraint(
        "ck_webhook_outbox_pending_schedule",
        "webhook_outbox",
        f"status <> 'pending' OR (next_attempt_at IS NOT NULL AND attempts < {_MAX_ATTEMPTS})",
    )
    op.create_check_constraint(
        "ck_webhook_outbox_terminal_schedule_empty",
        "webhook_outbox",
        "status = 'pending' OR next_attempt_at IS NULL",
    )
    op.create_check_constraint(
        "ck_webhook_outbox_failed_state",
        "webhook_outbox",
        f"status <> 'failed' OR (attempts = {_MAX_ATTEMPTS} AND length(trim(last_error)) > 0)",
    )
    op.create_check_constraint(
        "ck_webhook_outbox_discarded_reason",
        "webhook_outbox",
        "status <> 'discarded' OR length(trim(last_error)) > 0",
    )


def downgrade():
    op.drop_constraint("ck_webhook_outbox_discarded_reason", "webhook_outbox", type_="check")
    op.drop_constraint("ck_webhook_outbox_failed_state", "webhook_outbox", type_="check")
    op.drop_constraint("ck_webhook_outbox_terminal_schedule_empty", "webhook_outbox", type_="check")
    op.drop_constraint("ck_webhook_outbox_pending_schedule", "webhook_outbox", type_="check")
    op.drop_constraint("ck_webhook_outbox_attempts_bounded", "webhook_outbox", type_="check")
    op.drop_constraint("ck_webhook_outbox_status_valid", "webhook_outbox", type_="check")
    op.drop_constraint("ck_webhook_outbox_payload_nonempty", "webhook_outbox", type_="check")
    op.drop_constraint("ck_webhook_outbox_event_type_normalized", "webhook_outbox", type_="check")
    op.drop_constraint("ck_webhook_outbox_destination_nonempty", "webhook_outbox", type_="check")
    op.drop_constraint("ck_webhook_destinations_secret_length", "webhook_destinations", type_="check")
    op.drop_constraint("ck_webhook_destinations_event_type_normalized", "webhook_destinations", type_="check")
    op.drop_constraint("ck_webhook_destinations_url_normalized", "webhook_destinations", type_="check")
    op.drop_constraint("ck_webhook_destinations_name_normalized", "webhook_destinations", type_="check")
    op.drop_constraint("ck_webhook_destinations_name_nonempty", "webhook_destinations", type_="check")
