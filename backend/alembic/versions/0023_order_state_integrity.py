"""enforce order state integrity

Revision ID: 0023_order_state_integrity
Revises: 0022_payment_attempt_integrity
Create Date: 2026-07-30
"""

from alembic import op
import sqlalchemy as sa

revision = "0023_order_state_integrity"
down_revision = "0022_payment_attempt_integrity"
branch_labels = None
depends_on = None

_ORDER_STATUSES = (
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
)
_PAYMENT_STATUSES = (
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
)
_DELIVERY_STATUSES = (
    "not_started",
    "assembling",
    "ready",
    "shipped",
    "delivery_failed",
    "delivered",
    "returned",
    "cancelled",
)
_PAID_OPERATIONAL_STATUSES = ("paid", "assembling", "ready", "shipped", "completed")
_REFUND_IN_PROGRESS_PAYMENT_STATUSES = (
    "paid",
    "refund_processing",
    "refund_retry_required",
    "refund_pending",
    "refund_review_required",
)
_PAYMENT_REVIEW_STATUSES = ("payment_review_required", "paid_review_required")
_SHIPPED_DELIVERY_STATUSES = ("shipped", "delivery_failed", "returned")


def _sql_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade():
    order_statuses = _sql_values(_ORDER_STATUSES)
    payment_statuses = _sql_values(_PAYMENT_STATUSES)
    delivery_statuses = _sql_values(_DELIVERY_STATUSES)
    paid_operational = _sql_values(_PAID_OPERATIONAL_STATUSES)
    refund_in_progress = _sql_values(_REFUND_IN_PROGRESS_PAYMENT_STATUSES)
    payment_review = _sql_values(_PAYMENT_REVIEW_STATUSES)
    shipped_delivery = _sql_values(_SHIPPED_DELIVERY_STATUSES)

    op.execute(
        sa.text(
            f"""
            UPDATE orders
            SET status = CASE
                    WHEN lower(trim(coalesce(status, ''))) IN ({order_statuses})
                        THEN lower(trim(status))
                    ELSE 'payment_review_required'
                END,
                payment_status = CASE
                    WHEN lower(trim(coalesce(payment_status, ''))) IN ({payment_statuses})
                        THEN lower(trim(payment_status))
                    ELSE 'payment_review_required'
                END,
                delivery_status = CASE
                    WHEN lower(trim(coalesce(delivery_status, ''))) IN ({delivery_statuses})
                        THEN lower(trim(delivery_status))
                    ELSE 'not_started'
                END
            """
        )
    )

    # Provider/payment state is more authoritative than a stale order label.
    op.execute(
        sa.text(
            """
            UPDATE orders
            SET status = CASE
                    WHEN payment_status = 'cancelled' THEN 'cancelled'
                    WHEN payment_status = 'refunded' THEN 'refunded'
                    WHEN payment_status = 'partially_refunded' THEN 'partially_refunded'
                    WHEN payment_status IN (
                        'refund_processing',
                        'refund_retry_required',
                        'refund_pending',
                        'refund_review_required'
                    ) THEN 'refund_requested'
                    WHEN payment_status IN ('payment_review_required', 'paid_review_required')
                        THEN 'payment_review_required'
                    WHEN payment_status = 'payment_created'
                         AND status NOT IN ('paid', 'assembling', 'ready', 'shipped', 'completed')
                        THEN 'payment_created'
                    WHEN payment_status = 'pending'
                         AND status NOT IN ('created')
                        THEN 'created'
                    WHEN payment_status = 'paid'
                         AND status NOT IN (
                            'paid', 'assembling', 'ready', 'shipped', 'completed', 'refund_requested'
                         )
                        THEN 'paid'
                    ELSE status
                END
            """
        )
    )

    # Make the payment label exact for the repaired order state.
    op.execute(
        sa.text(
            f"""
            UPDATE orders
            SET payment_status = CASE
                    WHEN status = 'created' THEN 'pending'
                    WHEN status = 'payment_created' THEN 'payment_created'
                    WHEN status = 'payment_review_required'
                        THEN CASE
                            WHEN payment_status IN ({payment_review}) THEN payment_status
                            ELSE 'payment_review_required'
                        END
                    WHEN status IN ({paid_operational}) THEN 'paid'
                    WHEN status = 'refund_requested'
                        THEN CASE
                            WHEN payment_status IN ({refund_in_progress}) THEN payment_status
                            ELSE 'refund_review_required'
                        END
                    WHEN status = 'partially_refunded' THEN 'partially_refunded'
                    WHEN status = 'refunded' THEN 'refunded'
                    WHEN status = 'cancelled' THEN 'cancelled'
                    ELSE 'payment_review_required'
                END
            """
        )
    )

    # Operational order stages own the delivery stage; refund stages preserve
    # the already valid shipment history so post-delivery returns remain valid.
    op.execute(
        sa.text(
            f"""
            UPDATE orders
            SET delivery_status = CASE
                    WHEN status = 'assembling' THEN 'assembling'
                    WHEN status = 'ready'
                        THEN CASE WHEN delivery_status = 'cancelled' THEN 'cancelled' ELSE 'ready' END
                    WHEN status = 'shipped'
                        THEN CASE
                            WHEN delivery_status IN ({shipped_delivery}) THEN delivery_status
                            ELSE 'shipped'
                        END
                    WHEN status = 'completed' THEN 'delivered'
                    WHEN status = 'cancelled'
                        THEN CASE
                            WHEN delivery_status IN ('not_started', 'cancelled') THEN delivery_status
                            ELSE 'cancelled'
                        END
                    ELSE delivery_status
                END
            """
        )
    )

    op.create_check_constraint(
        "ck_orders_status_valid",
        "orders",
        f"status IN ({order_statuses})",
    )
    op.create_check_constraint(
        "ck_orders_payment_status_valid",
        "orders",
        f"payment_status IN ({payment_statuses})",
    )
    op.create_check_constraint(
        "ck_orders_delivery_status_valid",
        "orders",
        f"delivery_status IN ({delivery_statuses})",
    )
    op.create_check_constraint(
        "ck_orders_payment_state_coherent",
        "orders",
        f"""
        (
            (status = 'created' AND payment_status = 'pending')
            OR (status = 'payment_created' AND payment_status = 'payment_created')
            OR (status = 'payment_review_required' AND payment_status IN ({payment_review}))
            OR (status IN ({paid_operational}) AND payment_status = 'paid')
            OR (status = 'refund_requested' AND payment_status IN ({refund_in_progress}))
            OR (status = 'partially_refunded' AND payment_status = 'partially_refunded')
            OR (status = 'refunded' AND payment_status = 'refunded')
            OR (status = 'cancelled' AND payment_status = 'cancelled')
        )
        """,
    )
    op.create_check_constraint(
        "ck_orders_delivery_state_coherent",
        "orders",
        f"""
        (
            (status <> 'assembling' OR delivery_status = 'assembling')
            AND (status <> 'ready' OR delivery_status IN ('ready', 'cancelled'))
            AND (status <> 'shipped' OR delivery_status IN ({shipped_delivery}))
            AND (status <> 'completed' OR delivery_status = 'delivered')
            AND (status <> 'cancelled' OR delivery_status IN ('not_started', 'cancelled'))
        )
        """,
    )


def downgrade():
    op.drop_constraint("ck_orders_delivery_state_coherent", "orders", type_="check")
    op.drop_constraint("ck_orders_payment_state_coherent", "orders", type_="check")
    op.drop_constraint("ck_orders_delivery_status_valid", "orders", type_="check")
    op.drop_constraint("ck_orders_payment_status_valid", "orders", type_="check")
    op.drop_constraint("ck_orders_status_valid", "orders", type_="check")
