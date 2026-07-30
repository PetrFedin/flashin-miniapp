from __future__ import annotations

ORDER_STATUSES = frozenset(
    {
        "created",
        "payment_created",
        "payment_review_required",
        "paid",
        "assembling",
        "ready",
        "shipped",
        "completed",
        "refund_requested",
        "partially_refunded",
        "refunded",
        "cancelled",
    }
)
PAYMENT_STATUSES = frozenset(
    {
        "pending",
        "payment_created",
        "payment_review_required",
        "paid_review_required",
        "paid",
        "refund_processing",
        "refund_retry_required",
        "refund_pending",
        "refund_review_required",
        "partially_refunded",
        "refunded",
        "cancelled",
    }
)
DELIVERY_STATUSES = frozenset(
    {
        "not_started",
        "assembling",
        "ready",
        "shipped",
        "delivery_failed",
        "delivered",
        "returned",
        "cancelled",
    }
)

PAID_OPERATIONAL_ORDER_STATUSES = frozenset(
    {"paid", "assembling", "ready", "shipped", "completed"}
)
REFUND_IN_PROGRESS_PAYMENT_STATUSES = frozenset(
    {
        "paid",
        "refund_processing",
        "refund_retry_required",
        "refund_pending",
        "refund_review_required",
    }
)
PAYMENT_REVIEW_STATUSES = frozenset(
    {"payment_review_required", "paid_review_required"}
)
SHIPPED_DELIVERY_STATUSES = frozenset(
    {"shipped", "delivery_failed", "returned"}
)


def sql_values(values: frozenset[str]) -> str:
    return ", ".join(f"'{value}'" for value in sorted(values))


ORDER_STATUS_SQL = sql_values(ORDER_STATUSES)
PAYMENT_STATUS_SQL = sql_values(PAYMENT_STATUSES)
DELIVERY_STATUS_SQL = sql_values(DELIVERY_STATUSES)
PAID_OPERATIONAL_ORDER_STATUS_SQL = sql_values(PAID_OPERATIONAL_ORDER_STATUSES)
REFUND_IN_PROGRESS_PAYMENT_STATUS_SQL = sql_values(REFUND_IN_PROGRESS_PAYMENT_STATUSES)
PAYMENT_REVIEW_STATUS_SQL = sql_values(PAYMENT_REVIEW_STATUSES)
SHIPPED_DELIVERY_STATUS_SQL = sql_values(SHIPPED_DELIVERY_STATUSES)

ORDER_PAYMENT_COHERENCE_SQL = f"""
(
    (status = 'created' AND payment_status = 'pending')
    OR (status = 'payment_created' AND payment_status = 'payment_created')
    OR (status = 'payment_review_required' AND payment_status IN ({PAYMENT_REVIEW_STATUS_SQL}))
    OR (status IN ({PAID_OPERATIONAL_ORDER_STATUS_SQL}) AND payment_status = 'paid')
    OR (status = 'refund_requested' AND payment_status IN ({REFUND_IN_PROGRESS_PAYMENT_STATUS_SQL}))
    OR (status = 'partially_refunded' AND payment_status = 'partially_refunded')
    OR (status = 'refunded' AND payment_status = 'refunded')
    OR (status = 'cancelled' AND payment_status = 'cancelled')
)
""".strip()

ORDER_DELIVERY_COHERENCE_SQL = f"""
(
    (status <> 'assembling' OR delivery_status = 'assembling')
    AND (status <> 'ready' OR delivery_status IN ('ready', 'cancelled'))
    AND (status <> 'shipped' OR delivery_status IN ({SHIPPED_DELIVERY_STATUS_SQL}))
    AND (status <> 'completed' OR delivery_status = 'delivered')
    AND (status <> 'cancelled' OR delivery_status IN ('not_started', 'cancelled'))
)
""".strip()
