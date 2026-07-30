"""bound webhook destination and payload sizes

Revision ID: 0027_webhook_payload_limits
Revises: 0026_webhook_delivery_integrity
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa

revision = "0027_webhook_payload_limits"
down_revision = "0026_webhook_delivery_integrity"
branch_labels = None
depends_on = None

_MAX_PAYLOAD_CHARS = 256 * 1024
_MAX_ERROR_CHARS = 2000


def upgrade():
    op.execute(
        sa.text(
            f"""
            UPDATE webhook_outbox
            SET payload = jsonb_build_object(
                    'legacy_outbox_id', id,
                    'discard_reason', 'payload exceeded {_MAX_PAYLOAD_CHARS} characters'
                )::text,
                status = 'discarded',
                attempts = least(greatest(attempts, 0), 10),
                next_attempt_at = NULL,
                last_error = 'Discarded oversized legacy webhook payload'
            WHERE length(payload) > {_MAX_PAYLOAD_CHARS}
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            UPDATE webhook_outbox
            SET last_error = left(last_error, {_MAX_ERROR_CHARS})
            WHERE length(last_error) > {_MAX_ERROR_CHARS}
            """
        )
    )

    op.create_check_constraint(
        "ck_webhook_destinations_url_length",
        "webhook_destinations",
        "length(url) BETWEEN 1 AND 255",
    )
    op.create_check_constraint(
        "ck_webhook_destinations_event_type_length",
        "webhook_destinations",
        "length(event_type) BETWEEN 1 AND 120",
    )
    op.create_check_constraint(
        "ck_webhook_outbox_event_type_length",
        "webhook_outbox",
        "length(event_type) BETWEEN 1 AND 120",
    )
    op.create_check_constraint(
        "ck_webhook_outbox_payload_size",
        "webhook_outbox",
        f"length(payload) <= {_MAX_PAYLOAD_CHARS}",
    )
    op.create_check_constraint(
        "ck_webhook_outbox_error_size",
        "webhook_outbox",
        f"length(last_error) <= {_MAX_ERROR_CHARS}",
    )


def downgrade():
    op.drop_constraint("ck_webhook_outbox_error_size", "webhook_outbox", type_="check")
    op.drop_constraint("ck_webhook_outbox_payload_size", "webhook_outbox", type_="check")
    op.drop_constraint("ck_webhook_outbox_event_type_length", "webhook_outbox", type_="check")
    op.drop_constraint(
        "ck_webhook_destinations_event_type_length",
        "webhook_destinations",
        type_="check",
    )
    op.drop_constraint("ck_webhook_destinations_url_length", "webhook_destinations", type_="check")
