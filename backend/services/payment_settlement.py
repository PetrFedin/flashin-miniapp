from fastapi import HTTPException

from .event_dispatcher import emit_event
from .fulfillment import ensure_fulfillment_task
from .inventory import commit_reservations_to_sold
from .loyalty import (
    add_points,
    mark_redemption_committed,
    reward_referral_after_first_paid_order,
)
from .notifications import queue_order_paid
from .outbox import enqueue_event_for_destinations, enqueue_webhook
from .timeline import add_timeline_event


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


def _order_item_quantities(order) -> dict[int, int]:
    quantities: dict[int, int] = {}
    for item in order.items:
        quantities[item.variant_id] = quantities.get(item.variant_id, 0) + item.quantity
    return quantities


def settle_paid_order(db, order) -> bool:
    if order.payment_status in SETTLED_ORDER_PAYMENT_STATUSES:
        return False
    if order.status == "cancelled" or order.payment_status == "cancelled":
        raise HTTPException(status_code=409, detail="Cancelled order requires payment review")

    commit_reservations_to_sold(db, _order_item_quantities(order))
    queue_order_paid(db, order)

    if order.loyalty_points_redeemed:
        add_points(
            db,
            order.customer_id,
            -order.loyalty_points_redeemed,
            "loyalty_redeemed",
            order.id,
        )
        mark_redemption_committed(
            db,
            order.customer_id,
            None,
            order.id,
            order.loyalty_points_redeemed,
        )

    add_points(
        db,
        order.customer_id,
        round(order.total_amount * 0.01, 2),
        "order_paid",
        order.id,
    )
    reward_referral_after_first_paid_order(db, order.customer_id, order.id)
    add_timeline_event(
        db,
        order.customer_id,
        "order_paid",
        f"Заказ #{order.id} оплачен",
        {"total": order.total_amount},
    )
    ensure_fulfillment_task(db, order)

    event_payload = {"order_id": order.id, "total": order.total_amount}
    emit_event(db, "order.paid", "order", order.id, event_payload)
    enqueue_webhook(
        db,
        "internal://order-paid",
        "order.paid",
        event_payload,
    )
    enqueue_event_for_destinations(db, "order.paid", event_payload)

    order.payment_status = "paid"
    order.status = "paid"
    return True
