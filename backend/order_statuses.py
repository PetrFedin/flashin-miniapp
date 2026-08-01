"""Canonical business statuses and transitions for the order lifecycle.

Payment, refund, cancellation, and delivery statuses have dedicated workflows.
The generic admin order editor is intentionally limited to fulfillment progress
so it cannot impersonate provider-owned state transitions.
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

ADMIN_MANAGED_ORDER_TRANSITIONS = {
    "paid": frozenset({"assembling"}),
    "assembling": frozenset({"ready"}),
    "ready": frozenset({"shipped"}),
    "shipped": frozenset({"completed"}),
}

PROVIDER_OWNED_ORDER_STATUSES = frozenset(
    {
        "payment_created",
        "payment_review_required",
        "paid",
        "refund_requested",
        "partially_refunded",
        "refunded",
        "cancelled",
    }
)
