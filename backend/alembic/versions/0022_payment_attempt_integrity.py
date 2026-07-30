"""enforce payment creation attempt integrity

Revision ID: 0022_payment_attempt_integrity
Revises: 0021_return_request_integrity
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa

revision = "0022_payment_attempt_integrity"
down_revision = "0021_return_request_integrity"
branch_labels = None
depends_on = None

_OPEN_STATUSES = ("creating", "retry_required", "review_required")
_VALID_STATUSES = (*_OPEN_STATUSES, "completed", "abandoned")
_ERROR_STATUSES = ("retry_required", "review_required", "abandoned")


def _sql_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade():
    open_statuses = _sql_values(_OPEN_STATUSES)
    valid_statuses = _sql_values(_VALID_STATUSES)
    error_statuses = _sql_values(_ERROR_STATUSES)

    op.execute(
        sa.text(
            """
            UPDATE payment_creation_attempts
            SET provider = CASE
                    WHEN length(trim(coalesce(provider, ''))) = 0 THEN 'yookassa'
                    ELSE lower(trim(provider))
                END,
                provider_payment_id = trim(coalesce(provider_payment_id, '')),
                last_error = trim(coalesce(last_error, ''))
            """
        )
    )
    op.execute(
        sa.text(
            """
            WITH renumbered AS (
                SELECT
                    id,
                    row_number() OVER (
                        PARTITION BY order_id, provider
                        ORDER BY created_at ASC, id ASC
                    ) AS normalized_number
                FROM payment_creation_attempts
            )
            UPDATE payment_creation_attempts AS attempt
            SET attempt_number = renumbered.normalized_number
            FROM renumbered
            WHERE attempt.id = renumbered.id
              AND attempt.attempt_number <> renumbered.normalized_number
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            UPDATE payment_creation_attempts
            SET status = CASE
                    WHEN lower(trim(coalesce(status, ''))) IN ({valid_statuses})
                        THEN lower(trim(status))
                    ELSE 'review_required'
                END,
                last_error = CASE
                    WHEN lower(trim(coalesce(status, ''))) IN ({valid_statuses})
                        THEN last_error
                    ELSE coalesce(nullif(last_error, ''), 'invalid legacy payment attempt status')
                END
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE payment_creation_attempts
            SET status = 'review_required',
                lease_expires_at = NULL,
                last_error = coalesce(nullif(last_error, ''), 'completed attempt has no provider payment id')
            WHERE status = 'completed'
              AND provider_payment_id = ''
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE payment_creation_attempts
            SET status = 'retry_required',
                lease_expires_at = NULL,
                last_error = coalesce(nullif(last_error, ''), 'creating attempt had no lease')
            WHERE status = 'creating'
              AND lease_expires_at IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            UPDATE payment_creation_attempts
            SET lease_expires_at = NULL,
                last_error = coalesce(nullif(last_error, ''), 'payment attempt requires operator action')
            WHERE status IN ({error_statuses})
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE payment_creation_attempts
            SET lease_expires_at = NULL
            WHERE status = 'completed'
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            WITH ranked AS (
                SELECT
                    id,
                    row_number() OVER (
                        PARTITION BY order_id, provider
                        ORDER BY
                            CASE status
                                WHEN 'review_required' THEN 0
                                WHEN 'creating' THEN 1
                                ELSE 2
                            END,
                            attempt_number DESC,
                            id DESC
                    ) AS open_rank
                FROM payment_creation_attempts
                WHERE status IN ({open_statuses})
            )
            UPDATE payment_creation_attempts AS attempt
            SET status = 'abandoned',
                lease_expires_at = NULL,
                last_error = coalesce(nullif(attempt.last_error, ''), 'superseded duplicate open attempt')
            FROM ranked
            WHERE attempt.id = ranked.id
              AND ranked.open_rank > 1
            """
        )
    )

    op.create_check_constraint(
        "ck_payment_creation_attempts_number_positive",
        "payment_creation_attempts",
        "attempt_number > 0",
    )
    op.create_check_constraint(
        "ck_payment_creation_attempts_provider_normalized",
        "payment_creation_attempts",
        "length(trim(provider)) > 0 AND provider = lower(trim(provider))",
    )
    op.create_check_constraint(
        "ck_payment_creation_attempts_status_valid",
        "payment_creation_attempts",
        f"status IN ({valid_statuses})",
    )
    op.create_check_constraint(
        "ck_payment_creation_attempts_creating_lease_required",
        "payment_creation_attempts",
        "status <> 'creating' OR lease_expires_at IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_payment_creation_attempts_noncreating_lease_empty",
        "payment_creation_attempts",
        "status = 'creating' OR lease_expires_at IS NULL",
    )
    op.create_check_constraint(
        "ck_payment_creation_attempts_completed_provider_id_required",
        "payment_creation_attempts",
        "status <> 'completed' OR length(trim(provider_payment_id)) > 0",
    )
    op.create_check_constraint(
        "ck_payment_creation_attempts_failure_error_required",
        "payment_creation_attempts",
        f"status NOT IN ({error_statuses}) OR length(trim(last_error)) > 0",
    )
    op.create_index(
        "uq_payment_creation_attempts_one_open",
        "payment_creation_attempts",
        ["order_id", "provider"],
        unique=True,
        postgresql_where=sa.text(f"status IN ({open_statuses})"),
    )


def downgrade():
    op.drop_index("uq_payment_creation_attempts_one_open", table_name="payment_creation_attempts")
    op.drop_constraint(
        "ck_payment_creation_attempts_failure_error_required",
        "payment_creation_attempts",
        type_="check",
    )
    op.drop_constraint(
        "ck_payment_creation_attempts_completed_provider_id_required",
        "payment_creation_attempts",
        type_="check",
    )
    op.drop_constraint(
        "ck_payment_creation_attempts_noncreating_lease_empty",
        "payment_creation_attempts",
        type_="check",
    )
    op.drop_constraint(
        "ck_payment_creation_attempts_creating_lease_required",
        "payment_creation_attempts",
        type_="check",
    )
    op.drop_constraint(
        "ck_payment_creation_attempts_status_valid",
        "payment_creation_attempts",
        type_="check",
    )
    op.drop_constraint(
        "ck_payment_creation_attempts_provider_normalized",
        "payment_creation_attempts",
        type_="check",
    )
    op.drop_constraint(
        "ck_payment_creation_attempts_number_positive",
        "payment_creation_attempts",
        type_="check",
    )
