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

# Financial settlement is shared by payment idempotency, referral eligibility,
# refunds and reconciliation. Keep the catalog in one dependency-neutral module
# so those workflows cannot silently drift apart.
SETTLED_ORDER_PAYMENT_STATUSES = frozenset(
    {
        "paid",
        "paid_review_required",
        "refund_processing",
        "refund_pending",
        "refund_review_required",
        "partially_refunded",
        "refunded",
    }
)
