from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.services import payment_settlement


def make_order(**overrides):
    values = {
        "id": 7,
        "customer_id": 3,
        "status": "payment_created",
        "payment_status": "payment_created",
        "total_amount": 1250.0,
        "loyalty_points_redeemed": 100.0,
        "items": [
            SimpleNamespace(variant_id=11, quantity=2),
            SimpleNamespace(variant_id=11, quantity=1),
            SimpleNamespace(variant_id=12, quantity=4),
        ],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def patch_side_effects(monkeypatch):
    calls = []

    monkeypatch.setattr(
        payment_settlement,
        "commit_reservations_to_sold",
        lambda db, quantities: calls.append(("inventory", quantities)),
    )
    monkeypatch.setattr(
        payment_settlement,
        "queue_order_paid",
        lambda db, order: calls.append(("notification", order.id)),
    )
    monkeypatch.setattr(
        payment_settlement,
        "add_points",
        lambda db, customer_id, points, reason, order_id: calls.append(
            ("points", customer_id, points, reason, order_id)
        ),
    )
    monkeypatch.setattr(
        payment_settlement,
        "mark_redemption_committed",
        lambda db, customer_id, cart_id, order_id, points: calls.append(
            ("redemption", customer_id, cart_id, order_id, points)
        ),
    )
    monkeypatch.setattr(
        payment_settlement,
        "reward_referral_after_first_paid_order",
        lambda db, customer_id, order_id: calls.append(("referral", customer_id, order_id)),
    )
    monkeypatch.setattr(
        payment_settlement,
        "add_timeline_event",
        lambda db, customer_id, event_type, title, payload: calls.append(
            ("timeline", customer_id, event_type, title, payload)
        ),
    )
    monkeypatch.setattr(
        payment_settlement,
        "ensure_fulfillment_task",
        lambda db, order: calls.append(("fulfillment", order.id)),
    )
    monkeypatch.setattr(
        payment_settlement,
        "emit_event",
        lambda db, event_type, entity_type, entity_id, payload: calls.append(
            ("event", event_type, entity_type, entity_id, payload)
        ),
    )
    monkeypatch.setattr(
        payment_settlement,
        "enqueue_webhook",
        lambda db, destination, event_type, payload: calls.append(
            ("webhook", destination, event_type, payload)
        ),
    )
    monkeypatch.setattr(
        payment_settlement,
        "enqueue_event_for_destinations",
        lambda db, event_type, payload: calls.append(("destinations", event_type, payload)),
    )
    return calls


def test_paid_order_settlement_applies_all_side_effects_once(monkeypatch):
    calls = patch_side_effects(monkeypatch)
    order = make_order()
    db = object()

    assert payment_settlement.settle_paid_order(db, order) is True
    first_calls = list(calls)

    assert order.status == "paid"
    assert order.payment_status == "paid"
    assert ("inventory", {11: 3, 12: 4}) in calls
    assert ("points", 3, -100.0, "loyalty_redeemed", 7) in calls
    assert ("points", 3, 12.5, "order_paid", 7) in calls
    assert ("redemption", 3, None, 7, 100.0) in calls
    assert ("fulfillment", 7) in calls
    assert any(call[0] == "event" and call[1] == "order.paid" for call in calls)

    assert payment_settlement.settle_paid_order(db, order) is False
    assert calls == first_calls


def test_settlement_without_redeemed_points_skips_redemption_side_effects(monkeypatch):
    calls = patch_side_effects(monkeypatch)
    order = make_order(loyalty_points_redeemed=0)

    assert payment_settlement.settle_paid_order(object(), order) is True
    assert not any(call[0] == "redemption" for call in calls)
    assert not any(call[0] == "points" and call[3] == "loyalty_redeemed" for call in calls)
    assert any(call[0] == "points" and call[3] == "order_paid" for call in calls)


@pytest.mark.parametrize(
    "payment_status",
    [
        "paid",
        "paid_review_required",
        "refund_processing",
        "refund_pending",
        "refund_review_required",
        "partially_refunded",
        "refunded",
    ],
)
def test_already_settled_order_is_noop(monkeypatch, payment_status):
    calls = patch_side_effects(monkeypatch)
    order = make_order(payment_status=payment_status)

    assert payment_settlement.settle_paid_order(object(), order) is False
    assert calls == []


def test_cancelled_order_requires_review_before_settlement(monkeypatch):
    calls = patch_side_effects(monkeypatch)
    order = make_order(status="cancelled", payment_status="cancelled")

    with pytest.raises(HTTPException) as exc_info:
        payment_settlement.settle_paid_order(object(), order)

    assert exc_info.value.status_code == 409
    assert calls == []
