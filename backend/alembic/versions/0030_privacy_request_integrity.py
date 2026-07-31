"""enforce privacy request and consent integrity

Revision ID: 0030_privacy_request_integrity
Revises: 0029_catalog_moysklad_integrity
Create Date: 2026-07-31
"""

from alembic import op
import sqlalchemy as sa

revision = "0030_privacy_request_integrity"
down_revision = "0029_catalog_moysklad_integrity"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        sa.text(
            """
            UPDATE privacy_requests
            SET request_type = lower(trim(coalesce(request_type, ''))),
                status = lower(trim(coalesce(status, ''))),
                result_url = left(trim(coalesce(result_url, '')), 2048)
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE privacy_requests
            SET request_type = 'legacy_unknown',
                status = 'superseded',
                result_url = '',
                processed_at = coalesce(processed_at, created_at, CURRENT_TIMESTAMP)
            WHERE request_type NOT IN ('export', 'delete', 'consent_withdrawal')
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE privacy_requests
            SET status = 'superseded',
                result_url = '',
                processed_at = coalesce(processed_at, created_at, CURRENT_TIMESTAMP)
            WHERE status NOT IN ('requested', 'processing', 'processed', 'superseded')
            """
        )
    )
    # Processing was never committed as a durable leased state. A surviving
    # legacy row is therefore safe to return to the request queue.
    op.execute(
        sa.text(
            """
            UPDATE privacy_requests
            SET status = 'requested', result_url = '', processed_at = NULL
            WHERE status = 'processing'
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE privacy_requests
            SET result_url = '', processed_at = NULL
            WHERE status = 'requested'
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE privacy_requests
            SET processed_at = coalesce(processed_at, created_at, CURRENT_TIMESTAMP)
            WHERE status IN ('processed', 'superseded')
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE privacy_requests
            SET result_url = ''
            WHERE request_type <> 'export'
            """
        )
    )
    # Preserve the newest open request and mark older duplicates terminal.
    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY customer_id, request_type
                           ORDER BY created_at DESC NULLS LAST, id DESC
                       ) AS rn
                FROM privacy_requests
                WHERE status IN ('requested', 'processing')
            )
            UPDATE privacy_requests r
            SET status = 'superseded',
                result_url = '',
                processed_at = CURRENT_TIMESTAMP
            FROM ranked d
            WHERE r.id = d.id AND d.rn > 1
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE consent_records
            SET consent_type = lower(trim(coalesce(consent_type, ''))),
                source = lower(trim(coalesce(source, '')))
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE consent_records
            SET consent_type = 'legacy_unknown', source = 'legacy_quarantine'
            WHERE consent_type NOT IN (
                'analytics', 'marketing', 'personalization', 'privacy', 'terms'
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE consent_records
            SET source = CASE
                    WHEN source = '' THEN 'legacy_unknown'
                    ELSE left(source, 120)
                END
            """
        )
    )

    op.create_check_constraint(
        "ck_privacy_requests_type_valid",
        "privacy_requests",
        "request_type IN ('consent_withdrawal', 'delete', 'export', 'legacy_unknown')",
    )
    op.create_check_constraint(
        "ck_privacy_requests_status_valid",
        "privacy_requests",
        "status IN ('processed', 'processing', 'requested', 'superseded')",
    )
    op.create_check_constraint(
        "ck_privacy_requests_type_normalized",
        "privacy_requests",
        "request_type = lower(trim(request_type))",
    )
    op.create_check_constraint(
        "ck_privacy_requests_status_normalized",
        "privacy_requests",
        "status = lower(trim(status))",
    )
    op.create_check_constraint(
        "ck_privacy_requests_result_url_normalized",
        "privacy_requests",
        "result_url = trim(result_url)",
    )
    op.create_check_constraint(
        "ck_privacy_requests_result_url_size",
        "privacy_requests",
        "length(result_url) <= 2048",
    )
    op.create_check_constraint(
        "ck_privacy_requests_state_coherent",
        "privacy_requests",
        "((status IN ('requested', 'processing') AND processed_at IS NULL AND result_url = '') "
        "OR (status IN ('processed', 'superseded') AND processed_at IS NOT NULL))",
    )
    op.create_check_constraint(
        "ck_privacy_requests_result_type_coherent",
        "privacy_requests",
        "request_type = 'export' OR result_url = ''",
    )
    op.create_index(
        "uq_privacy_requests_open_customer_type",
        "privacy_requests",
        ["customer_id", "request_type"],
        unique=True,
        postgresql_where=sa.text("status IN ('requested', 'processing')"),
    )

    op.create_check_constraint(
        "ck_consent_records_type_valid",
        "consent_records",
        "consent_type IN ('analytics', 'legacy_unknown', 'marketing', 'personalization', 'privacy', 'terms')",
    )
    op.create_check_constraint(
        "ck_consent_records_type_normalized",
        "consent_records",
        "consent_type = lower(trim(consent_type))",
    )
    op.create_check_constraint(
        "ck_consent_records_source_normalized",
        "consent_records",
        "source = lower(trim(source))",
    )
    op.create_check_constraint(
        "ck_consent_records_source_size",
        "consent_records",
        "length(source) BETWEEN 1 AND 120",
    )


def downgrade():
    for name in (
        "ck_consent_records_source_size",
        "ck_consent_records_source_normalized",
        "ck_consent_records_type_normalized",
        "ck_consent_records_type_valid",
    ):
        op.drop_constraint(name, "consent_records", type_="check")

    op.drop_index(
        "uq_privacy_requests_open_customer_type",
        table_name="privacy_requests",
    )
    for name in (
        "ck_privacy_requests_result_type_coherent",
        "ck_privacy_requests_state_coherent",
        "ck_privacy_requests_result_url_size",
        "ck_privacy_requests_result_url_normalized",
        "ck_privacy_requests_status_normalized",
        "ck_privacy_requests_type_normalized",
        "ck_privacy_requests_status_valid",
        "ck_privacy_requests_type_valid",
    ):
        op.drop_constraint(name, "privacy_requests", type_="check")
