"""enforce return request state integrity

Revision ID: 0021_return_request_integrity
Revises: 0020_promo_code_integrity
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa

revision = "0021_return_request_integrity"
down_revision = "0020_promo_code_integrity"
branch_labels = None
depends_on = None

_OPEN_STATUSES = (
    "requested",
    "processing",
    "refund_retry_required",
    "refund_review_required",
    "refund_pending",
)
_VALID_STATUSES = (*_OPEN_STATUSES, "approved", "approved_partial", "failed")
_AMOUNT_REQUIRED_STATUSES = (
    "processing",
    "refund_retry_required",
    "refund_review_required",
    "refund_pending",
    "approved",
    "approved_partial",
)
_PROVIDER_LINKED_STATUSES = ("refund_pending", "approved", "approved_partial")


def _sql_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade():
    valid_statuses = _sql_values(_VALID_STATUSES)
    open_statuses = _sql_values(_OPEN_STATUSES)
    amount_required_statuses = _sql_values(_AMOUNT_REQUIRED_STATUSES)
    provider_linked_statuses = _sql_values(_PROVIDER_LINKED_STATUSES)

    op.execute(
        sa.text(
            f"""
            UPDATE return_requests
            SET reason = CASE
                    WHEN length(trim(coalesce(reason, ''))) < 5
                        THEN 'Legacy return request #' || id::text
                    ELSE left(trim(reason), 2000)
                END,
                status = CASE
                    WHEN lower(trim(coalesce(status, ''))) IN ({valid_statuses})
                        THEN lower(trim(status))
                    ELSE 'failed'
                END,
                provider_refund_id = trim(coalesce(provider_refund_id, '')),
                refund_amount = greatest(coalesce(refund_amount, 0), 0)
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            UPDATE return_requests
            SET status = CASE
                    WHEN refund_amount > 0 THEN 'refund_review_required'
                    ELSE 'failed'
                END
            WHERE status IN ('approved', 'approved_partial')
              AND (refund_amount <= 0 OR provider_refund_id = '')
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE return_requests
            SET status = 'refund_review_required'
            WHERE status = 'refund_pending'
              AND provider_refund_id = ''
              AND refund_amount > 0
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            UPDATE return_requests
            SET status = 'failed'
            WHERE status IN ({amount_required_statuses})
              AND refund_amount <= 0
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
                        PARTITION BY order_id
                        ORDER BY
                            CASE WHEN provider_refund_id <> '' THEN 0 ELSE 1 END,
                            CASE status
                                WHEN 'refund_pending' THEN 0
                                WHEN 'refund_review_required' THEN 1
                                WHEN 'processing' THEN 2
                                WHEN 'refund_retry_required' THEN 3
                                ELSE 4
                            END,
                            created_at DESC,
                            id DESC
                    ) AS open_rank
                FROM return_requests
                WHERE status IN ({open_statuses})
            )
            UPDATE return_requests AS request
            SET status = 'failed'
            FROM ranked
            WHERE request.id = ranked.id
              AND ranked.open_rank > 1
            """
        )
    )

    op.create_check_constraint(
        "ck_return_requests_reason_length",
        "return_requests",
        "length(trim(reason)) BETWEEN 5 AND 2000",
    )
    op.create_check_constraint(
        "ck_return_requests_reason_normalized",
        "return_requests",
        "reason = trim(reason)",
    )
    op.create_check_constraint(
        "ck_return_requests_status_valid",
        "return_requests",
        f"status IN ({valid_statuses})",
    )
    op.create_check_constraint(
        "ck_return_requests_amount_required",
        "return_requests",
        f"status NOT IN ({amount_required_statuses}) OR refund_amount > 0",
    )
    op.create_check_constraint(
        "ck_return_requests_provider_id_normalized",
        "return_requests",
        "provider_refund_id = trim(provider_refund_id)",
    )
    op.create_check_constraint(
        "ck_return_requests_provider_id_required",
        "return_requests",
        f"status NOT IN ({provider_linked_statuses}) OR length(provider_refund_id) > 0",
    )
    op.create_index(
        "uq_return_requests_one_open_per_order",
        "return_requests",
        ["order_id"],
        unique=True,
        postgresql_where=sa.text(f"status IN ({open_statuses})"),
    )


def downgrade():
    op.drop_index("uq_return_requests_one_open_per_order", table_name="return_requests")
    op.drop_constraint("ck_return_requests_provider_id_required", "return_requests", type_="check")
    op.drop_constraint("ck_return_requests_provider_id_normalized", "return_requests", type_="check")
    op.drop_constraint("ck_return_requests_amount_required", "return_requests", type_="check")
    op.drop_constraint("ck_return_requests_status_valid", "return_requests", type_="check")
    op.drop_constraint("ck_return_requests_reason_normalized", "return_requests", type_="check")
    op.drop_constraint("ck_return_requests_reason_length", "return_requests", type_="check")
