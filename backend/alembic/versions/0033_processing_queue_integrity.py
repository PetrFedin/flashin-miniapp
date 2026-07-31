"""enforce processing queue integrity

Revision ID: 0033_processing_queue_integrity
Revises: 0032_scheduled_job_run_integrity
Create Date: 2026-07-31
"""

from alembic import op
import sqlalchemy as sa

revision = "0033_processing_queue_integrity"
down_revision = "0032_scheduled_job_run_integrity"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "business_events",
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "business_events",
        sa.Column("lease_token", sa.String(length=32), nullable=False, server_default=""),
    )
    op.add_column(
        "business_events",
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "business_events",
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
    )

    op.execute(
        """
        UPDATE business_events
        SET status = lower(trim(COALESCE(status, ''))),
            attempts = LEAST(GREATEST(COALESCE(attempts, 0), 0), 10),
            lease_token = '',
            lease_expires_at = NULL,
            last_error = left(trim(COALESCE(last_error, '')), 2000)
        """
    )
    op.execute(
        """
        UPDATE business_events
        SET attempts = LEAST(attempts + 1, 10),
            status = CASE WHEN attempts + 1 >= 10 THEN 'failed' ELSE 'pending' END,
            last_error = 'Recovered legacy processing event'
        WHERE status = 'processing'
        """
    )
    op.execute(
        """
        UPDATE business_events
        SET status = 'failed',
            attempts = 10,
            processed_at = NULL,
            last_error = CASE
                WHEN length(trim(last_error)) > 0 THEN last_error
                ELSE 'Legacy event had an invalid status'
            END
        WHERE status NOT IN ('pending', 'processed', 'failed')
        """
    )
    op.execute(
        """
        UPDATE business_events
        SET status = 'failed',
            attempts = 10,
            processed_at = NULL,
            last_error = CASE
                WHEN length(trim(last_error)) > 0 THEN last_error
                ELSE 'Legacy event exhausted retry attempts'
            END
        WHERE status = 'pending' AND attempts >= 10
        """
    )
    op.execute(
        """
        UPDATE business_events
        SET next_attempt_at = COALESCE(created_at, CURRENT_TIMESTAMP),
            processed_at = NULL,
            lease_token = '',
            lease_expires_at = NULL,
            last_error = CASE
                WHEN attempts = 0 THEN ''
                WHEN length(trim(last_error)) > 0 THEN last_error
                ELSE 'Legacy event retry'
            END
        WHERE status = 'pending'
        """
    )
    op.execute(
        """
        UPDATE business_events
        SET processed_at = COALESCE(processed_at, created_at, CURRENT_TIMESTAMP),
            next_attempt_at = NULL,
            lease_token = '',
            lease_expires_at = NULL,
            last_error = ''
        WHERE status = 'processed'
        """
    )
    op.execute(
        """
        UPDATE business_events
        SET attempts = 10,
            processed_at = NULL,
            next_attempt_at = NULL,
            lease_token = '',
            lease_expires_at = NULL,
            last_error = CASE
                WHEN length(trim(last_error)) > 0 THEN last_error
                ELSE 'Legacy event failed'
            END
        WHERE status = 'failed'
        """
    )

    op.create_check_constraint(
        "ck_business_events_status_valid",
        "business_events",
        "status IN ('failed', 'pending', 'processed', 'processing')",
    )
    op.create_check_constraint(
        "ck_business_events_attempts_range",
        "business_events",
        "attempts BETWEEN 0 AND 10",
    )
    op.create_check_constraint(
        "ck_business_events_lease_token_size",
        "business_events",
        "length(lease_token) IN (0, 32)",
    )
    op.create_check_constraint(
        "ck_business_events_error_size",
        "business_events",
        "length(last_error) <= 2000",
    )
    op.create_check_constraint(
        "ck_business_events_state_coherent",
        "business_events",
        "((status = 'pending' AND attempts < 10 AND processed_at IS NULL "
        "AND next_attempt_at IS NOT NULL AND lease_token = '' AND lease_expires_at IS NULL "
        "AND (attempts = 0 OR length(trim(last_error)) > 0)) "
        "OR (status = 'processing' AND attempts < 10 AND processed_at IS NULL "
        "AND next_attempt_at IS NULL AND length(lease_token) = 32 AND lease_expires_at IS NOT NULL) "
        "OR (status = 'processed' AND processed_at IS NOT NULL AND next_attempt_at IS NULL "
        "AND lease_token = '' AND lease_expires_at IS NULL AND last_error = '') "
        "OR (status = 'failed' AND attempts = 10 AND processed_at IS NULL "
        "AND next_attempt_at IS NULL AND lease_token = '' AND lease_expires_at IS NULL "
        "AND length(trim(last_error)) > 0))",
    )
    op.create_index(
        "ix_business_events_due",
        "business_events",
        ["status", "next_attempt_at", "id"],
    )

    op.add_column(
        "media_processing_jobs",
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "media_processing_jobs",
        sa.Column("lease_token", sa.String(length=32), nullable=False, server_default=""),
    )
    op.add_column(
        "media_processing_jobs",
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
    )

    op.execute(
        """
        UPDATE media_processing_jobs
        SET status = lower(trim(COALESCE(status, ''))),
            attempts = LEAST(GREATEST(COALESCE(attempts, 0), 0), 5),
            lease_token = '',
            lease_expires_at = NULL,
            last_error = left(trim(COALESCE(last_error, '')), 2000)
        """
    )
    op.execute(
        """
        UPDATE media_processing_jobs
        SET attempts = LEAST(attempts + 1, 5),
            status = CASE WHEN attempts + 1 >= 5 THEN 'failed' ELSE 'pending' END,
            last_error = 'Recovered legacy processing media job'
        WHERE status = 'processing'
        """
    )
    op.execute(
        """
        UPDATE media_processing_jobs
        SET status = 'failed',
            attempts = 5,
            processed_at = NULL,
            last_error = CASE
                WHEN length(trim(last_error)) > 0 THEN last_error
                ELSE 'Legacy media job had an invalid status'
            END
        WHERE status NOT IN ('pending', 'processed', 'failed')
        """
    )
    op.execute(
        """
        UPDATE media_processing_jobs
        SET status = 'failed',
            attempts = 5,
            processed_at = NULL,
            last_error = CASE
                WHEN length(trim(last_error)) > 0 THEN last_error
                ELSE 'Legacy media job exhausted retry attempts'
            END
        WHERE status = 'pending' AND attempts >= 5
        """
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY media_asset_id
                       ORDER BY id ASC
                   ) AS row_number
            FROM media_processing_jobs
            WHERE status = 'pending'
        )
        UPDATE media_processing_jobs AS jobs
        SET status = 'failed',
            attempts = 5,
            processed_at = NULL,
            next_attempt_at = NULL,
            lease_token = '',
            lease_expires_at = NULL,
            last_error = 'Legacy duplicate active media job'
        FROM ranked
        WHERE jobs.id = ranked.id AND ranked.row_number > 1
        """
    )
    op.execute(
        """
        UPDATE media_processing_jobs
        SET next_attempt_at = COALESCE(created_at, CURRENT_TIMESTAMP),
            processed_at = NULL,
            lease_token = '',
            lease_expires_at = NULL,
            last_error = CASE
                WHEN attempts = 0 THEN ''
                WHEN length(trim(last_error)) > 0 THEN last_error
                ELSE 'Legacy media job retry'
            END
        WHERE status = 'pending'
        """
    )
    op.execute(
        """
        UPDATE media_processing_jobs
        SET processed_at = COALESCE(processed_at, created_at, CURRENT_TIMESTAMP),
            next_attempt_at = NULL,
            lease_token = '',
            lease_expires_at = NULL,
            last_error = ''
        WHERE status = 'processed'
        """
    )
    op.execute(
        """
        UPDATE media_processing_jobs
        SET attempts = 5,
            processed_at = NULL,
            next_attempt_at = NULL,
            lease_token = '',
            lease_expires_at = NULL,
            last_error = CASE
                WHEN length(trim(last_error)) > 0 THEN last_error
                ELSE 'Legacy media job failed'
            END
        WHERE status = 'failed'
        """
    )

    op.create_check_constraint(
        "ck_media_processing_jobs_status_valid",
        "media_processing_jobs",
        "status IN ('failed', 'pending', 'processed', 'processing')",
    )
    op.create_check_constraint(
        "ck_media_processing_jobs_attempts_range",
        "media_processing_jobs",
        "attempts BETWEEN 0 AND 5",
    )
    op.create_check_constraint(
        "ck_media_processing_jobs_lease_token_size",
        "media_processing_jobs",
        "length(lease_token) IN (0, 32)",
    )
    op.create_check_constraint(
        "ck_media_processing_jobs_error_size",
        "media_processing_jobs",
        "length(last_error) <= 2000",
    )
    op.create_check_constraint(
        "ck_media_processing_jobs_state_coherent",
        "media_processing_jobs",
        "((status = 'pending' AND attempts < 5 AND processed_at IS NULL "
        "AND next_attempt_at IS NOT NULL AND lease_token = '' AND lease_expires_at IS NULL "
        "AND (attempts = 0 OR length(trim(last_error)) > 0)) "
        "OR (status = 'processing' AND attempts < 5 AND processed_at IS NULL "
        "AND next_attempt_at IS NULL AND length(lease_token) = 32 AND lease_expires_at IS NOT NULL) "
        "OR (status = 'processed' AND processed_at IS NOT NULL AND next_attempt_at IS NULL "
        "AND lease_token = '' AND lease_expires_at IS NULL AND last_error = '') "
        "OR (status = 'failed' AND attempts = 5 AND processed_at IS NULL "
        "AND next_attempt_at IS NULL AND lease_token = '' AND lease_expires_at IS NULL "
        "AND length(trim(last_error)) > 0))",
    )
    op.create_index(
        "ix_media_processing_jobs_due",
        "media_processing_jobs",
        ["status", "next_attempt_at", "id"],
    )
    op.create_index(
        "uq_media_processing_jobs_active_asset",
        "media_processing_jobs",
        ["media_asset_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'processing')"),
    )


def downgrade():
    op.drop_index(
        "uq_media_processing_jobs_active_asset",
        table_name="media_processing_jobs",
    )
    op.drop_index("ix_media_processing_jobs_due", table_name="media_processing_jobs")
    op.drop_constraint(
        "ck_media_processing_jobs_state_coherent",
        "media_processing_jobs",
        type_="check",
    )
    op.drop_constraint(
        "ck_media_processing_jobs_error_size",
        "media_processing_jobs",
        type_="check",
    )
    op.drop_constraint(
        "ck_media_processing_jobs_lease_token_size",
        "media_processing_jobs",
        type_="check",
    )
    op.drop_constraint(
        "ck_media_processing_jobs_attempts_range",
        "media_processing_jobs",
        type_="check",
    )
    op.drop_constraint(
        "ck_media_processing_jobs_status_valid",
        "media_processing_jobs",
        type_="check",
    )
    op.drop_column("media_processing_jobs", "lease_expires_at")
    op.drop_column("media_processing_jobs", "lease_token")
    op.drop_column("media_processing_jobs", "next_attempt_at")

    op.drop_index("ix_business_events_due", table_name="business_events")
    op.drop_constraint(
        "ck_business_events_state_coherent",
        "business_events",
        type_="check",
    )
    op.drop_constraint(
        "ck_business_events_error_size",
        "business_events",
        type_="check",
    )
    op.drop_constraint(
        "ck_business_events_lease_token_size",
        "business_events",
        type_="check",
    )
    op.drop_constraint(
        "ck_business_events_attempts_range",
        "business_events",
        type_="check",
    )
    op.drop_constraint(
        "ck_business_events_status_valid",
        "business_events",
        type_="check",
    )
    op.drop_column("business_events", "last_error")
    op.drop_column("business_events", "lease_expires_at")
    op.drop_column("business_events", "lease_token")
    op.drop_column("business_events", "next_attempt_at")
