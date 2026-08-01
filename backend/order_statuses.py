"""Canonical business statuses for the order lifecycle.

Payment and delivery statuses have separate state domains. Keep this set limited
to values assigned to ``Order.status`` so API validation and tests share one
source of truth without depending on a route module.
"""

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
