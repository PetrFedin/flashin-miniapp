from __future__ import annotations

PENDING_WEBHOOK_STATUS = "pending"
SENT_WEBHOOK_STATUS = "sent"
FAILED_WEBHOOK_STATUS = "failed"
DISCARDED_WEBHOOK_STATUS = "discarded"

WEBHOOK_OUTBOX_STATUSES = frozenset(
    {
        PENDING_WEBHOOK_STATUS,
        SENT_WEBHOOK_STATUS,
        FAILED_WEBHOOK_STATUS,
        DISCARDED_WEBHOOK_STATUS,
    }
)
TERMINAL_WEBHOOK_STATUSES = frozenset(
    {
        SENT_WEBHOOK_STATUS,
        FAILED_WEBHOOK_STATUS,
        DISCARDED_WEBHOOK_STATUS,
    }
)
MAX_WEBHOOK_ATTEMPTS = 10
MAX_WEBHOOK_BODY_BYTES = 256 * 1024


def sql_values(values: frozenset[str]) -> str:
    return ", ".join(f"'{value}'" for value in sorted(values))


WEBHOOK_OUTBOX_STATUS_SQL = sql_values(WEBHOOK_OUTBOX_STATUSES)
TERMINAL_WEBHOOK_STATUS_SQL = sql_values(TERMINAL_WEBHOOK_STATUSES)
