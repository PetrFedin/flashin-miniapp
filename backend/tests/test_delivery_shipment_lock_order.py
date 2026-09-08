import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.services import delivery_locking as locking
from backend.services import delivery_providers as delivery_service


def test_transition_locks_order_before_delivery_shipment(monkeypatch):
    events: list[tuple[str, int]] = []
    order = SimpleNamespace(id=101)
    shipment = SimpleNamespace(id=17, order_id=101)

    monkeypatch.setattr(locking, "_load_delivery_order_id", lambda _db, _shipment_id: 101)

    def lock_order(_db, order_id):
        events.append(("order", order_id))
        return order

    def lock_shipment(_db, shipment_id):
        events.append(("shipment", shipment_id))
        return shipment

    monkeypatch.setattr(locking, "_select_delivery_order_for_update", lock_order)
    monkeypatch.setattr(locking, "_select_delivery_shipment_for_update", lock_shipment)

    locked_order, locked_shipment = locking.lock_delivery_shipment_for_update(object(), 17)

    assert locked_order is order
    assert locked_shipment is shipment
    assert events == [("order", 101), ("shipment", 17)]


def test_missing_shipment_snapshot_takes_no_root_lock(monkeypatch):
    monkeypatch.setattr(locking, "_load_delivery_order_id", lambda _db, _shipment_id: None)
    monkeypatch.setattr(
        locking,
        "_select_delivery_order_for_update",
        lambda *_args, **_kwargs: pytest.fail("missing shipment must not lock an order"),
    )
    monkeypatch.setattr(
        locking,
        "_select_delivery_shipment_for_update",
        lambda *_args, **_kwargs: pytest.fail("missing shipment must not be locked"),
    )

    with pytest.raises(HTTPException) as exc_info:
        locking.lock_delivery_shipment_for_update(object(), 17)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Shipment not found"


def test_missing_order_does_not_lock_delivery_shipment(monkeypatch):
    monkeypatch.setattr(locking, "_load_delivery_order_id", lambda _db, _shipment_id: 101)
    monkeypatch.setattr(locking, "_select_delivery_order_for_update", lambda _db, _order_id: None)
    monkeypatch.setattr(
        locking,
        "_select_delivery_shipment_for_update",
        lambda *_args, **_kwargs: pytest.fail(
            "shipment must not be locked before missing order is rejected"
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        locking.lock_delivery_shipment_for_update(object(), 17)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Shipment is linked to a missing order"


def test_shipment_disappears_after_snapshot_fails_closed(monkeypatch):
    monkeypatch.setattr(locking, "_load_delivery_order_id", lambda _db, _shipment_id: 101)
    monkeypatch.setattr(
        locking,
        "_select_delivery_order_for_update",
        lambda _db, _order_id: SimpleNamespace(id=101),
    )
    monkeypatch.setattr(locking, "_select_delivery_shipment_for_update", lambda _db, _shipment_id: None)

    with pytest.raises(HTTPException) as exc_info:
        locking.lock_delivery_shipment_for_update(object(), 17)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Shipment changed while being locked"


def test_shipment_order_change_fails_closed(monkeypatch):
    monkeypatch.setattr(locking, "_load_delivery_order_id", lambda _db, _shipment_id: 101)
    monkeypatch.setattr(
        locking,
        "_select_delivery_order_for_update",
        lambda _db, _order_id: SimpleNamespace(id=101),
    )
    monkeypatch.setattr(
        locking,
        "_select_delivery_shipment_for_update",
        lambda _db, _shipment_id: SimpleNamespace(id=17, order_id=202),
    )

    with pytest.raises(HTTPException) as exc_info:
        locking.lock_delivery_shipment_for_update(object(), 17)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Shipment changed while being locked"


def test_transition_service_rejects_mismatched_prelocked_root(monkeypatch):
    monkeypatch.setattr(
        delivery_service,
        "enqueue_moysklad_demand",
        lambda *_args, **_kwargs: pytest.fail("mismatched root must fail before side effects"),
    )
    monkeypatch.setattr(
        delivery_service,
        "queue_order_status",
        lambda *_args, **_kwargs: pytest.fail("mismatched root must fail before side effects"),
    )
    order = SimpleNamespace(id=101, status="ready", delivery_status="ready")
    shipment = SimpleNamespace(
        id=17,
        order_id=202,
        status="created",
        tracking_number="",
    )

    with pytest.raises(ValueError, match="Shipment changed while being updated"):
        delivery_service.transition_shipment(
            object(),
            order,
            shipment,
            "TRACK-101",
            "shipped",
        )


def test_idempotent_same_status_consumes_prelocked_order_without_requery(monkeypatch):
    class NoQueryDb:
        def query(self, *_args, **_kwargs):
            raise AssertionError("transition service must not reacquire Order after DeliveryShipment")

    monkeypatch.setattr(
        delivery_service,
        "enqueue_moysklad_demand",
        lambda *_args, **_kwargs: pytest.fail("idempotent transition must not enqueue demand"),
    )
    monkeypatch.setattr(
        delivery_service,
        "queue_order_status",
        lambda *_args, **_kwargs: pytest.fail("idempotent transition must not enqueue notification"),
    )
    order = SimpleNamespace(id=101, status="shipped", delivery_status="shipped")
    shipment = SimpleNamespace(
        id=17,
        order_id=101,
        status="shipped",
        tracking_number="TRACK-101",
    )

    returned = delivery_service.transition_shipment(
        NoQueryDb(),
        order,
        shipment,
        "DIFFERENT-TRACK",
        "shipped",
    )

    assert returned is order
    assert shipment.tracking_number == "TRACK-101"


def test_shipped_transition_uses_prelocked_order_without_requery(monkeypatch):
    class NoQueryDb:
        def query(self, *_args, **_kwargs):
            raise AssertionError("transition service must not reacquire Order after DeliveryShipment")

    demand_ids: list[int] = []
    notified_ids: list[int] = []
    monkeypatch.setattr(
        delivery_service,
        "enqueue_moysklad_demand",
        lambda _db, order_id: demand_ids.append(order_id),
    )
    monkeypatch.setattr(
        delivery_service,
        "queue_order_status",
        lambda _db, order: notified_ids.append(order.id),
    )
    order = SimpleNamespace(
        id=101,
        status="ready",
        delivery_status="ready",
        tracking_number="",
    )
    shipment = SimpleNamespace(
        id=17,
        order_id=101,
        status="created",
        tracking_number="",
        updated_at=None,
    )

    returned = delivery_service.transition_shipment(
        NoQueryDb(),
        order,
        shipment,
        "TRACK-101",
        "shipped",
    )

    assert returned is order
    assert shipment.status == "shipped"
    assert shipment.tracking_number == "TRACK-101"
    assert order.status == "shipped"
    assert order.delivery_status == "shipped"
    assert order.tracking_number == "TRACK-101"
    assert demand_ids == [101]
    assert notified_ids == [101]


@pytest.mark.skipif(
    not os.getenv("DATABASE_URL", "").startswith("postgresql"),
    reason="requires PostgreSQL row-lock semantics",
)
def test_postgres_delivery_shipment_lock_order_smoke():
    from scripts.delivery_shipment_lock_order_smoke import main

    assert main() == 0
