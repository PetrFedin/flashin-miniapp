from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base, utcnow_naive
from backend.models import Customer, Order
from backend.pilot_models import PilotOrderSlot, PilotRuntimeState
import backend.services.pilot_runtime as pilot_runtime

ROOT = Path(__file__).resolve().parents[2]


def _session(*, status: str = "active", accepted_orders: int = 1, with_slot: bool = True):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    customer = Customer(telegram_id="123456")
    db.add(customer)
    db.flush()
    order = Order(customer_id=customer.id, total_amount=100, currency="RUB")
    db.add(order)
    db.flush()

    state = PilotRuntimeState(
        id=1,
        run_id="pilot-run",
        status=status,
        admission_sha256="a" * 64,
        release_sha256="r" * 64,
        pilot_state_created_at="2026-08-12T00:00:00Z",
        pilot_state_revision=1,
        pilot_state_sha256="b" * 64,
        max_orders=20,
        accepted_orders=accepted_orders,
        allowed_telegram_ids='["123456"]',
        opened_at=utcnow_naive(),
    )
    db.add(state)
    if with_slot:
        db.add(
            PilotOrderSlot(
                run_id=state.run_id,
                sequence=accepted_orders,
                order_id=order.id,
                customer_id=customer.id,
                admission_sha256=state.admission_sha256,
            )
        )
    db.commit()
    settings = SimpleNamespace(
        pilot_runtime_enforced=True,
        pilot_runtime_max_orders=20,
    )
    return db, order, state, settings


def _healthy_runtime(monkeypatch):
    monkeypatch.setattr(
        pilot_runtime,
        "_verify_runtime_safety",
        lambda *args, **kwargs: {"revision": 2, "sha256": "c" * 64},
    )


def test_active_pilot_order_allows_fresh_payment_and_advances_runtime_anchor(monkeypatch):
    db, order, _state, settings = _session()
    _healthy_runtime(monkeypatch)

    pilot_runtime.assert_pilot_new_payment_attempt_allowed(
        db,
        order_id=order.id,
        settings=settings,
    )

    state = db.get(PilotRuntimeState, 1)
    assert state.pilot_state_revision == 2
    assert state.pilot_state_sha256 == "c" * 64


def test_completed_twentieth_order_can_finish_payment_lifecycle(monkeypatch):
    db, order, _state, settings = _session(status="completed", accepted_orders=20)
    _healthy_runtime(monkeypatch)

    pilot_runtime.assert_pilot_new_payment_attempt_allowed(
        db,
        order_id=order.id,
        settings=settings,
    )


def test_stopped_runtime_blocks_fresh_payment_before_runtime_verification(monkeypatch):
    db, order, _state, settings = _session(status="stopped")

    def should_not_run(*args, **kwargs):
        raise AssertionError("stopped runtime must block before safety verification")

    monkeypatch.setattr(pilot_runtime, "_verify_runtime_safety", should_not_run)
    with pytest.raises(HTTPException) as blocked:
        pilot_runtime.assert_pilot_new_payment_attempt_allowed(
            db,
            order_id=order.id,
            settings=settings,
        )

    assert blocked.value.status_code == 423
    assert blocked.value.detail["code"] == "pilot_payment_attempt_unavailable"


def test_enforced_pilot_blocks_fresh_payment_for_order_without_slot(monkeypatch):
    db, order, _state, settings = _session(with_slot=False)
    _healthy_runtime(monkeypatch)

    with pytest.raises(HTTPException) as blocked:
        pilot_runtime.assert_pilot_new_payment_attempt_allowed(
            db,
            order_id=order.id,
            settings=settings,
        )

    assert blocked.value.status_code == 423
    assert blocked.value.detail["code"] == "pilot_payment_attempt_unavailable"


def test_slot_runtime_binding_mismatch_fails_closed(monkeypatch):
    db, order, state, settings = _session()
    _healthy_runtime(monkeypatch)
    state.run_id = "other-run"
    db.commit()

    with pytest.raises(HTTPException) as failed:
        pilot_runtime.assert_pilot_new_payment_attempt_allowed(
            db,
            order_id=order.id,
            settings=settings,
        )

    assert failed.value.status_code == 503
    assert failed.value.detail["code"] == "pilot_runtime_integrity_failure"


def test_disabled_pilot_does_not_restrict_normal_payment(monkeypatch):
    db, order, _state, settings = _session(with_slot=False)
    settings.pilot_runtime_enforced = False

    def should_not_run(*args, **kwargs):
        raise AssertionError("disabled pilot must not query runtime safety")

    monkeypatch.setattr(pilot_runtime, "_verify_runtime_safety", should_not_run)
    pilot_runtime.assert_pilot_new_payment_attempt_allowed(
        db,
        order_id=order.id,
        settings=settings,
    )


def test_provider_create_is_guarded_but_existing_attempt_reconciliation_and_refunds_remain_available():
    service = (ROOT / "backend/services/payments.py").read_text(encoding="utf-8")
    api = (ROOT / "backend/api/payments.py").read_text(encoding="utf-8")

    create_start = service.index("async def create_yookassa_payment")
    guard = service.index("guard_pilot_new_payment_attempt", create_start)
    provider_post = service.index('"POST",\n        "/payments"', guard)
    refund_start = service.index("async def create_yookassa_refund")
    refund_end = service.index("async def fetch_yookassa_refund", refund_start)

    assert create_start < guard < provider_post < refund_start
    assert "guard_pilot_new_payment_attempt" not in service[refund_start:refund_end]

    endpoint_start = api.index("async def create_payment")
    reconcile = api.index("_reconcile_existing_payment(order, latest_payment)", endpoint_start)
    fresh_create = api.index("create_yookassa_payment(", reconcile)
    assert reconcile < fresh_create
