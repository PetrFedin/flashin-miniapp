"""enforce notification delivery integrity

Revision ID: 0028_notification_delivery_integrity
Revises: 0027_webhook_payload_limits
Create Date: 2026-07-31
"""

from alembic import op
import sqlalchemy as sa

revision = "0028_notification_delivery_integrity"
down_revision = "0027_webhook_payload_limits"
branch_labels = None
depends_on = None

_MAX_ATTEMPTS = 10
_MAX_MESSAGE_CHARS = 4096
_MAX_ERROR_CHARS = 2000


def upgrade():
    op.add_column(
        "notification_delivery_states",
        sa.Column(
            "deduplication_key",
            sa.String(length=255),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "notification_delivery_states",
        sa.Column(
            "lease_token",
            sa.String(length=64),
            nullable=False,
            server_default="",
        ),
    )

    # Normalize legacy values before any stricter constraints are enabled.
    op.execute(
        sa.text(
            f"""
            UPDATE notifications
            SET telegram_id = left(trim(coalesce(telegram_id, '')), 64),
                message = CASE
                    WHEN length(trim(coalesce(message, ''))) > {_MAX_MESSAGE_CHARS}
                        THEN left(trim(message), {_MAX_MESSAGE_CHARS - 1}) || '…'
                    ELSE trim(coalesce(message, ''))
                END,
                status = lower(trim(coalesce(status, ''))),
                error = left(trim(coalesce(error, '')), {_MAX_ERROR_CHARS})
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE notifications
            SET status = 'discarded',
                telegram_id = 'deleted:' || id::text,
                sent_at = NULL,
                error = 'Discarded invalid legacy Telegram chat id'
            WHERE telegram_id = ''
               OR telegram_id !~ '^-?[1-9][0-9]*$'
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE notifications
            SET status = 'discarded',
                message = '[discarded empty legacy notification #' || id::text || ']',
                sent_at = NULL,
                error = 'Discarded empty legacy notification message'
            WHERE message = ''
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE notifications
            SET status = 'discarded',
                sent_at = NULL,
                error = 'Discarded unknown legacy notification status'
            WHERE status NOT IN ('pending', 'processing', 'sent', 'failed', 'discarded')
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE notifications
            SET status = 'pending',
                sent_at = NULL,
                error = CASE
                    WHEN error = '' THEN 'Recovered expired legacy processing lease'
                    ELSE error
                END
            WHERE status = 'processing'
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE notifications
            SET status = 'discarded',
                sent_at = NULL,
                error = 'Discarded legacy sent notification without sent_at'
            WHERE status = 'sent' AND sent_at IS NULL
            """
        )
    )
    op.execute(sa.text("UPDATE notifications SET error = '' WHERE status = 'sent'"))
    op.execute(sa.text("UPDATE notifications SET sent_at = NULL WHERE status <> 'sent'"))
    op.execute(
        sa.text(
            """
            UPDATE notifications
            SET error = CASE
                WHEN status = 'failed' THEN 'Legacy notification delivery failed'
                ELSE 'Legacy notification was discarded'
            END
            WHERE status IN ('failed', 'discarded') AND error = ''
            """
        )
    )

    op.execute(
        sa.text(
            f"""
            UPDATE notification_delivery_states
            SET attempts = least(greatest(coalesce(attempts, 0), 0), {_MAX_ATTEMPTS}),
                last_error = left(trim(coalesce(last_error, '')), {_MAX_ERROR_CHARS}),
                deduplication_key = trim(coalesce(deduplication_key, '')),
                lease_token = '',
                updated_at = coalesce(updated_at, CURRENT_TIMESTAMP)
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO notification_delivery_states (
                notification_id,
                attempts,
                next_attempt_at,
                last_error,
                deduplication_key,
                lease_token,
                updated_at
            )
            SELECT n.id,
                   CASE WHEN n.status = 'failed' THEN 1 ELSE 0 END,
                   CASE WHEN n.status = 'pending' THEN CURRENT_TIMESTAMP ELSE NULL END,
                   CASE WHEN n.status = 'failed' THEN n.error ELSE '' END,
                   '',
                   '',
                   CURRENT_TIMESTAMP
            FROM notifications n
            LEFT JOIN notification_delivery_states s ON s.notification_id = n.id
            WHERE s.id IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            UPDATE notification_delivery_states s
            SET attempts = CASE
                    WHEN n.status = 'failed' AND s.attempts = 0 THEN 1
                    ELSE least(s.attempts, {_MAX_ATTEMPTS})
                END,
                next_attempt_at = CASE
                    WHEN n.status = 'pending' THEN coalesce(s.next_attempt_at, CURRENT_TIMESTAMP)
                    ELSE NULL
                END,
                last_error = CASE
                    WHEN (CASE
                            WHEN n.status = 'failed' AND s.attempts = 0 THEN 1
                            ELSE least(s.attempts, {_MAX_ATTEMPTS})
                          END) > 0
                        AND trim(s.last_error) = ''
                    THEN CASE
                        WHEN trim(n.error) <> '' THEN n.error
                        ELSE 'Legacy notification delivery attempt failed'
                    END
                    ELSE s.last_error
                END,
                lease_token = '',
                updated_at = CURRENT_TIMESTAMP
            FROM notifications n
            WHERE n.id = s.notification_id
            """
        )
    )

    op.drop_constraint(
        "ck_notification_delivery_state_attempts_nonnegative",
        "notification_delivery_states",
        type_="check",
    )

    op.create_check_constraint(
        "ck_notifications_status_valid",
        "notifications",
        "status IN ('discarded', 'failed', 'pending', 'processing', 'sent')",
    )
    op.create_check_constraint(
        "ck_notifications_telegram_id_size",
        "notifications",
        "length(telegram_id) BETWEEN 1 AND 64",
    )
    op.create_check_constraint(
        "ck_notifications_telegram_id_normalized",
        "notifications",
        "telegram_id = trim(telegram_id)",
    )
    op.create_check_constraint(
        "ck_notifications_message_size",
        "notifications",
        f"length(message) BETWEEN 1 AND {_MAX_MESSAGE_CHARS}",
    )
    op.create_check_constraint(
        "ck_notifications_message_normalized",
        "notifications",
        "message = trim(message)",
    )
    op.create_check_constraint(
        "ck_notifications_error_size",
        "notifications",
        f"length(error) <= {_MAX_ERROR_CHARS}",
    )
    op.create_check_constraint(
        "ck_notifications_sent_state_coherent",
        "notifications",
        "((status = 'sent' AND sent_at IS NOT NULL AND error = '') "
        "OR (status <> 'sent' AND sent_at IS NULL))",
    )
    op.create_check_constraint(
        "ck_notifications_terminal_error_required",
        "notifications",
        "status NOT IN ('failed', 'discarded') OR length(trim(error)) > 0",
    )

    op.create_check_constraint(
        "ck_notification_delivery_state_attempts_range",
        "notification_delivery_states",
        f"attempts BETWEEN 0 AND {_MAX_ATTEMPTS}",
    )
    op.create_check_constraint(
        "ck_notification_delivery_state_error_size",
        "notification_delivery_states",
        f"length(last_error) <= {_MAX_ERROR_CHARS}",
    )
    op.create_check_constraint(
        "ck_notification_delivery_state_deduplication_key_size",
        "notification_delivery_states",
        "length(deduplication_key) <= 255",
    )
    op.create_check_constraint(
        "ck_notification_delivery_state_deduplication_key_normalized",
        "notification_delivery_states",
        "deduplication_key = trim(deduplication_key)",
    )
    op.create_check_constraint(
        "ck_notification_delivery_state_lease_token_size",
        "notification_delivery_states",
        "length(lease_token) <= 64",
    )
    op.create_check_constraint(
        "ck_notification_delivery_state_lease_token_normalized",
        "notification_delivery_states",
        "lease_token = trim(lease_token)",
    )
    op.create_check_constraint(
        "ck_notification_delivery_state_lease_has_deadline",
        "notification_delivery_states",
        "lease_token = '' OR next_attempt_at IS NOT NULL",
    )
    op.create_check_constraint(
        "ck_notification_delivery_state_attempt_error_coherent",
        "notification_delivery_states",
        "attempts = 0 OR length(trim(last_error)) > 0",
    )
    op.create_index(
        "ix_notification_delivery_states_due",
        "notification_delivery_states",
        ["next_attempt_at", "notification_id"],
    )
    op.create_index(
        "uq_notification_delivery_states_deduplication_key",
        "notification_delivery_states",
        ["deduplication_key"],
        unique=True,
        postgresql_where=sa.text("deduplication_key <> ''"),
    )


def downgrade():
    op.drop_index(
        "uq_notification_delivery_states_deduplication_key",
        table_name="notification_delivery_states",
    )
    op.drop_index(
        "ix_notification_delivery_states_due",
        table_name="notification_delivery_states",
    )
    op.drop_constraint(
        "ck_notification_delivery_state_attempt_error_coherent",
        "notification_delivery_states",
        type_="check",
    )
    op.drop_constraint(
        "ck_notification_delivery_state_lease_has_deadline",
        "notification_delivery_states",
        type_="check",
    )
    op.drop_constraint(
        "ck_notification_delivery_state_lease_token_normalized",
        "notification_delivery_states",
        type_="check",
    )
    op.drop_constraint(
        "ck_notification_delivery_state_lease_token_size",
        "notification_delivery_states",
        type_="check",
    )
    op.drop_constraint(
        "ck_notification_delivery_state_deduplication_key_normalized",
        "notification_delivery_states",
        type_="check",
    )
    op.drop_constraint(
        "ck_notification_delivery_state_deduplication_key_size",
        "notification_delivery_states",
        type_="check",
    )
    op.drop_constraint(
        "ck_notification_delivery_state_error_size",
        "notification_delivery_states",
        type_="check",
    )
    op.drop_constraint(
        "ck_notification_delivery_state_attempts_range",
        "notification_delivery_states",
        type_="check",
    )
    op.create_check_constraint(
        "ck_notification_delivery_state_attempts_nonnegative",
        "notification_delivery_states",
        "attempts >= 0",
    )

    op.drop_constraint(
        "ck_notifications_terminal_error_required",
        "notifications",
        type_="check",
    )
    op.drop_constraint(
        "ck_notifications_sent_state_coherent",
        "notifications",
        type_="check",
    )
    op.drop_constraint("ck_notifications_error_size", "notifications", type_="check")
    op.drop_constraint(
        "ck_notifications_message_normalized",
        "notifications",
        type_="check",
    )
    op.drop_constraint("ck_notifications_message_size", "notifications", type_="check")
    op.drop_constraint(
        "ck_notifications_telegram_id_normalized",
        "notifications",
        type_="check",
    )
    op.drop_constraint(
        "ck_notifications_telegram_id_size",
        "notifications",
        type_="check",
    )
    op.drop_constraint("ck_notifications_status_valid", "notifications", type_="check")

    op.drop_column("notification_delivery_states", "lease_token")
    op.drop_column("notification_delivery_states", "deduplication_key")
