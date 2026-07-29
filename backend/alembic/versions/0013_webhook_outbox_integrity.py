"""webhook destination and outbox integrity

Revision ID: 0013_webhook_outbox_integrity
Revises: 0012_notification_delivery_retry_state
Create Date: 2026-07-29
"""

from alembic import op


revision = "0013_webhook_outbox_integrity"
down_revision = "0012_notification_delivery_retry_state"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE webhook_destinations SET event_type = '*' WHERE btrim(event_type) = ''")
    op.execute("DELETE FROM webhook_destinations WHERE btrim(url) = ''")
    op.execute(
        """
        DELETE FROM webhook_destinations duplicate
        USING webhook_destinations retained
        WHERE duplicate.id > retained.id
          AND duplicate.url = retained.url
          AND duplicate.event_type = retained.event_type
        """
    )
    op.execute("UPDATE webhook_outbox SET attempts = 0 WHERE attempts < 0")

    op.create_unique_constraint(
        "uq_webhook_destinations_url_event_type",
        "webhook_destinations",
        ["url", "event_type"],
    )
    op.create_check_constraint(
        "ck_webhook_destinations_url_nonempty",
        "webhook_destinations",
        "length(trim(url)) > 0",
    )
    op.create_check_constraint(
        "ck_webhook_destinations_event_type_nonempty",
        "webhook_destinations",
        "length(trim(event_type)) > 0",
    )
    op.create_check_constraint(
        "ck_webhook_outbox_attempts_nonnegative",
        "webhook_outbox",
        "attempts >= 0",
    )
    op.create_index(
        "ix_webhook_outbox_due",
        "webhook_outbox",
        ["status", "next_attempt_at", "id"],
    )


def downgrade():
    op.drop_index("ix_webhook_outbox_due", table_name="webhook_outbox")
    op.drop_constraint(
        "ck_webhook_outbox_attempts_nonnegative",
        "webhook_outbox",
        type_="check",
    )
    op.drop_constraint(
        "ck_webhook_destinations_event_type_nonempty",
        "webhook_destinations",
        type_="check",
    )
    op.drop_constraint(
        "ck_webhook_destinations_url_nonempty",
        "webhook_destinations",
        type_="check",
    )
    op.drop_constraint(
        "uq_webhook_destinations_url_event_type",
        "webhook_destinations",
        type_="unique",
    )
