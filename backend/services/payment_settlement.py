from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException

from ..order_statuses import SETTLED_ORDER_PAYMENT_STATUSES
from .event_dispatcher import emit_event
from .fulfillment import ensure_fulfillment_task
from .inventory import commit_reservations_to_sold
from .loyalty import (
    add_points,
    mark_redemption_committed,
    reward_referral_after_first_paid_order,
)
from .moysklad_outbound import enqueue_moysklad_customer_order
from .notifications import queue_order_paid
from .outbox import enqueue_event_for_destinations, enqueue_webhook
from .timeline import add_timeline_event

_POINTS_STEP = Decimal("0.0001")


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

    commit_reservations_to_sold(
        db,
        _order_item_quantities(order),
        order_id=order.id,
        source="payment_settlement",
    )
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

    earned_points = (Decimal(str(order.total_amount)) * Decimal("0.01")).quantize(
        _POINTS_STEP,
        rounding=ROUND_HALF_UP,
    )
    add_points(
        db,
        order.customer_id,
        earned_points,
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
    enqueue_moysklad_customer_order(db, order.id)
    return True
