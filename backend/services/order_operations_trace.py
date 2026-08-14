from __future__ import annotations

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..business_event_models import BusinessEventRecoveryState
from ..checkout_models import CheckoutAttempt
from ..database import utcnow_naive
from ..models import (
    BusinessEvent,
    FulfillmentTask,
    InventoryMovement,
    Notification,
    Order,
    Payment,
    PaymentEvent,
    ReturnRequest,
    SlaEvent,
)
from ..notification_models import NotificationDeliveryState, NotificationEventKey
from ..provider_models import ProviderCommand


def _iso(value):
    return value.isoformat() if value else None


def _provider_command(command: ProviderCommand) -> dict[str, object]:
    return {
        "id": int(command.id),
        "provider": str(command.provider),
        "command_type": str(command.command_type),
        "aggregate_type": str(command.aggregate_type),
        "aggregate_id": str(command.aggregate_id),
        "status": str(command.status),
        "attempts": int(command.attempts),
        "external_id": str(command.external_id or ""),
        "created_at": _iso(command.created_at),
        "completed_at": _iso(command.completed_at),
    }


def _inventory_movement(movement: InventoryMovement) -> dict[str, object]:
    return {
        "id": int(movement.id),
        "variant_id": int(movement.variant_id),
        "kind": str(movement.kind),
        "quantity": int(movement.quantity),
        "stock_before": int(movement.stock_before),
        "stock_after": int(movement.stock_after),
        "reserved_before": int(movement.reserved_before),
        "reserved_after": int(movement.reserved_after),
        "created_at": _iso(movement.created_at),
    }


def _inventory_movement_invalid(movement: InventoryMovement) -> bool:
    return bool(
        int(movement.quantity) <= 0
        or int(movement.stock_before) < 0
        or int(movement.stock_after) < 0
        or int(movement.reserved_before) < 0
        or int(movement.reserved_after) < 0
        or int(movement.reserved_before) > int(movement.stock_before)
        or int(movement.reserved_after) > int(movement.stock_after)
    )


def build_order_operations_trace(db: Session, order_id: int) -> dict[str, object] | None:
    """Build a read-only, non-secret incident trace for one order.

    ``order_id`` is the durable correlation key across asynchronous provider,
    inventory and fulfillment work. The trace intentionally excludes raw
    provider payloads, idempotency keys, request fingerprints, notification
    bodies, Telegram ids, free-form error text and other credentials/PII fields.
    """

    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        return None

    checkout = (
        db.query(CheckoutAttempt)
        .filter(CheckoutAttempt.order_id == order_id)
        .order_by(CheckoutAttempt.created_at.asc(), CheckoutAttempt.id.asc())
        .first()
    )
    payments = (
        db.query(Payment)
        .filter(Payment.order_id == order_id)
        .order_by(Payment.created_at.asc(), Payment.id.asc())
        .all()
    )
    provider_payment_ids = [
        str(payment.provider_payment_id)
        for payment in payments
        if str(payment.provider_payment_id or "").strip()
    ]
    payment_events = []
    if provider_payment_ids:
        payment_events = (
            db.query(PaymentEvent)
            .filter(PaymentEvent.provider_payment_id.in_(provider_payment_ids))
            .order_by(PaymentEvent.created_at.asc(), PaymentEvent.id.asc())
            .all()
        )

    returns = (
        db.query(ReturnRequest)
        .filter(ReturnRequest.order_id == order_id)
        .order_by(ReturnRequest.created_at.asc(), ReturnRequest.id.asc())
        .all()
    )
    return_ids = [int(item.id) for item in returns]

    provider_filter = and_(
        ProviderCommand.aggregate_type == "order",
        ProviderCommand.aggregate_id == str(order_id),
    )
    if return_ids:
        provider_filter = or_(
            provider_filter,
            and_(
                ProviderCommand.aggregate_type == "return",
                ProviderCommand.aggregate_id.in_([str(value) for value in return_ids]),
            ),
        )
    provider_commands = (
        db.query(ProviderCommand)
        .filter(provider_filter)
        .order_by(ProviderCommand.created_at.asc(), ProviderCommand.id.asc())
        .all()
    )

    inventory_movements = (
        db.query(InventoryMovement)
        .filter(InventoryMovement.order_id == order_id)
        .order_by(InventoryMovement.created_at.asc(), InventoryMovement.id.asc())
        .all()
    )

    fulfillment = (
        db.query(FulfillmentTask)
        .filter(FulfillmentTask.order_id == order_id)
        .order_by(FulfillmentTask.created_at.asc(), FulfillmentTask.id.asc())
        .all()
    )
    sla_events = (
        db.query(SlaEvent)
        .filter(SlaEvent.order_id == order_id)
        .order_by(SlaEvent.created_at.asc(), SlaEvent.id.asc())
        .all()
    )

    business_filter = and_(
        BusinessEvent.aggregate_type == "order",
        BusinessEvent.aggregate_id == str(order_id),
    )
    if return_ids:
        business_filter = or_(
            business_filter,
            and_(
                BusinessEvent.aggregate_type == "return",
                BusinessEvent.aggregate_id.in_([str(value) for value in return_ids]),
            ),
        )
    business_events = (
        db.query(BusinessEvent)
        .filter(business_filter)
        .order_by(BusinessEvent.created_at.asc(), BusinessEvent.id.asc())
        .all()
    )
    recovery_by_event_id: dict[int, BusinessEventRecoveryState] = {}
    if business_events:
        recovery_rows = (
            db.query(BusinessEventRecoveryState)
            .filter(
                BusinessEventRecoveryState.business_event_id.in_(
                    [int(event.id) for event in business_events]
                )
            )
            .all()
        )
        recovery_by_event_id = {
            int(row.business_event_id): row for row in recovery_rows
        }

    notification_rows = (
        db.query(Notification, NotificationDeliveryState, NotificationEventKey)
        .join(
            NotificationEventKey,
            NotificationEventKey.notification_id == Notification.id,
        )
        .outerjoin(
            NotificationDeliveryState,
            NotificationDeliveryState.notification_id == Notification.id,
        )
        .filter(NotificationEventKey.event_key.like(f"order:{order_id}:%"))
        .order_by(Notification.created_at.asc(), Notification.id.asc())
        .all()
    )

    actionable_provider_commands = sum(
        1
        for command in provider_commands
        if command.status in {"pending", "processing", "failed", "review_required"}
    )
    provider_failures = sum(
        1
        for command in provider_commands
        if command.status in {"failed", "review_required"}
    )
    inventory_invalid_rows = sum(
        1 for movement in inventory_movements if _inventory_movement_invalid(movement)
    )
    failed_notifications = sum(
        1
        for notification, _state, _event in notification_rows
        if notification.status == "failed"
    )
    unresolved_business_events = sum(
        1
        for event in business_events
        if event.status in {"pending", "processing", "failed"}
    )
    failed_business_events = sum(
        1 for event in business_events if event.status == "failed"
    )
    now = utcnow_naive()
    overdue_sla_events = sum(
        1
        for event in sla_events
        if event.status == "open" and event.due_at is not None and event.due_at <= now
    )

    return {
        "schema_version": 2,
        "correlation": {"type": "order_id", "value": str(order_id)},
        "order": {
            "id": int(order.id),
            "customer_id": int(order.customer_id),
            "status": str(order.status),
            "payment_status": str(order.payment_status),
            "delivery_status": str(order.delivery_status),
            "total_amount": float(order.total_amount),
            "currency": str(order.currency),
            "created_at": _iso(order.created_at),
        },
        "checkout": (
            {
                "attempt_id": int(checkout.id),
                "cart_id": int(checkout.cart_id),
                "created_at": _iso(checkout.created_at),
            }
            if checkout
            else None
        ),
        "payments": [
            {
                "id": int(payment.id),
                "provider": str(payment.provider),
                "provider_payment_id": str(payment.provider_payment_id or ""),
                "status": str(payment.status),
                "amount": float(payment.amount),
                "created_at": _iso(payment.created_at),
            }
            for payment in payments
        ],
        "payment_events": [
            {
                "id": int(event.id),
                "provider": str(event.provider),
                "provider_payment_id": str(event.provider_payment_id),
                "event_type": str(event.event_type),
                "processed": bool(event.processed),
                "created_at": _iso(event.created_at),
            }
            for event in payment_events
        ],
        "returns": [
            {
                "id": int(item.id),
                "status": str(item.status),
                "provider_refund_id": str(item.provider_refund_id or ""),
                "refund_amount": float(item.refund_amount),
                "created_at": _iso(item.created_at),
            }
            for item in returns
        ],
        "provider_commands": [_provider_command(command) for command in provider_commands],
        "inventory": [
            _inventory_movement(movement) for movement in inventory_movements
        ],
        "fulfillment": [
            {
                "id": int(task.id),
                "status": str(task.status),
                "assigned_admin_id": (
                    int(task.assigned_admin_id)
                    if task.assigned_admin_id is not None
                    else None
                ),
                "pick_started_at": _iso(task.pick_started_at),
                "packed_at": _iso(task.packed_at),
                "ready_at": _iso(task.ready_at),
                "created_at": _iso(task.created_at),
            }
            for task in fulfillment
        ],
        "business_events": [
            {
                "id": int(event.id),
                "event_type": str(event.event_type),
                "aggregate_type": str(event.aggregate_type),
                "aggregate_id": str(event.aggregate_id),
                "status": str(event.status),
                "attempts": int(event.attempts),
                "created_at": _iso(event.created_at),
                "processed_at": _iso(event.processed_at),
                "recovery": (
                    {
                        "failed_at": _iso(recovery_by_event_id[int(event.id)].failed_at),
                        "resolved_at": _iso(recovery_by_event_id[int(event.id)].resolved_at),
                        "replay_count": int(
                            recovery_by_event_id[int(event.id)].replay_count
                        ),
                        "last_attempt_at": _iso(
                            recovery_by_event_id[int(event.id)].last_attempt_at
                        ),
                        "last_replayed_at": _iso(
                            recovery_by_event_id[int(event.id)].last_replayed_at
                        ),
                    }
                    if int(event.id) in recovery_by_event_id
                    else None
                ),
            }
            for event in business_events
        ],
        "notifications": [
            {
                "id": int(notification.id),
                "event_key": str(event.event_key),
                "status": str(notification.status),
                "attempts": int(state.attempts) if state else 0,
                "next_attempt_at": _iso(state.next_attempt_at) if state else None,
                "created_at": _iso(notification.created_at),
                "sent_at": _iso(notification.sent_at),
            }
            for notification, state, event in notification_rows
        ],
        "sla": [
            {
                "id": int(event.id),
                "event_type": str(event.event_type),
                "status": str(event.status),
                "due_at": _iso(event.due_at),
                "created_at": _iso(event.created_at),
                "resolved_at": _iso(event.resolved_at),
            }
            for event in sla_events
        ],
        "attention": {
            "provider_commands_actionable": actionable_provider_commands,
            "provider_failures": provider_failures,
            "inventory_invalid_rows": inventory_invalid_rows,
            "failed_notifications": failed_notifications,
            "business_events_unresolved": unresolved_business_events,
            "business_events_failed": failed_business_events,
            "overdue_sla": overdue_sla_events,
            "required": bool(
                provider_failures
                or inventory_invalid_rows
                or failed_notifications
                or failed_business_events
                or overdue_sla_events
            ),
        },
    }
