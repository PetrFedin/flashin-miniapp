from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api import admin as admin_api
from backend.models import Order
from backend.schemas import OrderStatusUpdate


class FakeQuery:
    def __init__(self, value):
        self.value = value

    def filter(self, *args, **kwargs):
        return self

    def with_for_update(self):
        return self

    def first(self):
        return self.value


class FakeSession:
    def __init__(self, order):
        self.order = order
        self.commits = 0
        self.rollbacks = 0

    def query(self, entity):
        assert entity is Order
        return FakeQuery(self.order)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def make_order(**overrides):
    values = {
        "id": 17,
        "status": "paid",
        "payment_status": "paid",
        "delivery_status": "not_started",
        "tracking_number": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def prepare(monkeypatch, order):
    queued = []
    audits = []
    monkeypatch.setattr(admin_api, "require_permission", lambda *_args: None)
    monkeypatch.setattr(admin_api, "queue_order_status", lambda _db, value: queued.append(value.id))
    monkeypatch.setattr(admin_api, "log_admin_action", lambda *args: audits.append(args))
    monkeypatch.setattr(admin_api, "_order_with_items", lambda _db, _order_id: order)
    return queued, audits


def test_admin_can_advance_fulfillment_and_logs_only_actual_changes(monkeypatch):
    order = make_order()
    db = FakeSession(order)
    queued, audits = prepare(monkeypatch, order)

    result = admin_api.admin_update_order(
        order.id,
        OrderStatusUpdate(
            status="  ASSEMBLING ",
            delivery_status="assembling",
            tracking_number=" TRK-17 ",
        ),
        admin=SimpleNamespace(id=3),
        db=db,
    )

    assert result is order
    assert order.status == "assembling"
    assert order.payment_status == "paid"
    assert order.delivery_status == "assembling"
    assert order.tracking_number == "TRK-17"
    assert db.commits == 1
    assert db.rollbacks == 0
    assert queued == [17]
    assert len(audits) == 1
    assert audits[0][-1] == {
        "from_status": "paid",
        "status": "assembling",
        "delivery_status": "assembling",
        "tracking_number": "TRK-17",
    }


def test_admin_generic_patch_rejects_provider_owned_status(monkeypatch):
    order = make_order(status="created", payment_status="pending")
    db = FakeSession(order)
    queued, audits = prepare(monkeypatch, order)

    with pytest.raises(HTTPException, match="created -> cancelled") as exc_info:
        admin_api.admin_update_order(
            order.id,
            OrderStatusUpdate(status="cancelled"),
            admin=SimpleNamespace(id=3),
            db=db,
        )

    assert exc_info.value.status_code == 409
    assert order.status == "created"
    assert order.payment_status == "pending"
    assert db.commits == 0
    assert db.rollbacks == 1
    assert queued == []
    assert audits == []


def test_admin_noop_update_does_not_notify_or_audit(monkeypatch):
    order = make_order(
        status="assembling",
        delivery_status="assembling",
        tracking_number="TRK-17",
    )
    db = FakeSession(order)
    queued, audits = prepare(monkeypatch, order)

    result = admin_api.admin_update_order(
        order.id,
        OrderStatusUpdate(
            status="assembling",
            delivery_status="assembling",
            tracking_number="TRK-17",
        ),
        admin=SimpleNamespace(id=3),
        db=db,
    )

    assert result is order
    assert db.commits == 0
    assert db.rollbacks == 1
    assert queued == []
    assert audits == []


def test_admin_order_update_source_has_no_legacy_cancellation_side_effects():
    source = (admin_api.Path(__file__).resolve().parents[1] / "api" / "admin.py").read_text(
        encoding="utf-8"
    ) if hasattr(admin_api, "Path") else None

    if source is None:
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "api" / "admin.py").read_text(
            encoding="utf-8"
        )

    assert "datetime.utcnow" not in source
    assert "release_variant" not in source
    assert "LoyaltyRedemptionHold" not in source
    assert "payload.status == \"cancelled\"" not in source
