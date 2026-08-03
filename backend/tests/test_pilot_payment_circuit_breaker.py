from pathlib import Path
import sys

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.api import payments as payment_api  # noqa: E402
from backend.database import Base  # noqa: E402
from backend.models import Customer, Order, Payment  # noqa: E402
from backend.pilot_models import PilotOrderSlot, PilotRuntimeState  # noqa: E402
from backend.services import pilot_circuit_breaker as circuit  # noqa: E402
from backend.services.payment_reconciliation import create_reconciliation_row  # noqa: E402


def _database():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    return engine, factory


def _pilot_order(factory, *, active: bool = True):
    db = factory()
    customer = Customer(telegram_id="123456")
    db.add(customer)
    db.flush()
    order = Order(customer_id=customer.id, total_amount=100, currency="RUB")
    db.add(order)
    db.flush()
    state = PilotRuntimeState(
        id=1,
        run_id="pilot-run",
        status="active" if active else "stopped",
        admission_sha256="a" * 64,
        release_sha256="b" * 64,
        max_orders=20,
        accepted_orders=1,
        allowed_telegram_ids='["123456"]',
    )
    db.add(state)
    db.add(
        PilotOrderSlot(
            run_id="pilot-run",
            sequence=1,
            order_id=order.id,
            customer_id=customer.id,
            admission_sha256="a" * 64,
        )
    )
    db.commit()
    return db, order


def test_stop_pilot_for_order_is_scoped_and_idempotent():
    _engine, factory = _database()
    db, order = _pilot_order(factory)

    first = circuit.stop_pilot_for_order(
        db,
        order_id=order.id,
        reason="Provider Amount Mismatch ! secret text",
    )
    db.commit()
    state = db.get(PilotRuntimeState, 1)

    assert first.pilot_order is True
    assert first.changed is True
    assert state.status == "stopped"
    assert state.stop_reason == "auto:provider_amount_mismatch_secret_text"

    second = circuit.stop_pilot_for_order(
        db,
        order_id=order.id,
        reason="another_reason",
    )
    db.commit()
    assert second.pilot_order is True
    assert second.changed is False
    assert state.stop_reason == "auto:provider_amount_mismatch_secret_text"

    outsider = Order(customer_id=order.customer_id, total_amount=50, currency="RUB")
    db.add(outsider)
    db.commit()
    result = circuit.stop_pilot_for_order(
        db,
        order_id=outsider.id,
        reason="not_pilot",
    )
    assert result.pilot_order is False
    assert result.changed is False


def test_durable_trip_uses_independent_transaction_after_payment_rollback(monkeypatch):
    _engine, factory = _database()
    db, order = _pilot_order(factory)
    order_id = order.id
    db.rollback()
    db.close()

    monkeypatch.setattr(circuit, "SessionLocal", factory)
    result = circuit.trip_pilot_circuit_breaker(
        order_id=order_id,
        reason="provider_payment_amount_or_currency_mismatch",
    )

    check = factory()
    state = check.get(PilotRuntimeState, 1)
    assert result.changed is True
    assert state.status == "stopped"
    assert state.stop_reason == "auto:provider_payment_amount_or_currency_mismatch"
    check.close()


def test_payment_review_stops_pilot_in_same_transaction(monkeypatch):
    _engine, factory = _database()
    db, order = _pilot_order(factory)
    emitted = []
    queued = []
    monkeypatch.setattr(payment_api, "emit_event", lambda *args: emitted.append(args))
    monkeypatch.setattr(payment_api, "enqueue_webhook", lambda *args: queued.append(args))

    payment_api._queue_payment_review(db, order, "provider-payment", "paid_after_cancel")
    db.commit()

    state = db.get(PilotRuntimeState, 1)
    assert state.status == "stopped"
    assert state.stop_reason == "auto:payment_review:paid_after_cancel"
    assert len(emitted) == 1
    assert len(queued) == 1


def test_provider_amount_integrity_error_has_stable_reason():
    order = Order(id=7, customer_id=1, total_amount=100, currency="RUB")

    with pytest.raises(payment_api.ProviderPaymentIntegrityError) as mismatch:
        payment_api._validate_provider_amount(
            {"amount": {"value": "99.99", "currency": "RUB"}},
            order,
        )
    assert mismatch.value.reason == "provider_payment_amount_or_currency_mismatch"

    with pytest.raises(payment_api.ProviderPaymentIntegrityError) as invalid:
        payment_api._validate_provider_amount(
            {"amount": {"value": "not-a-number", "currency": "RUB"}},
            order,
        )
    assert invalid.value.reason == "provider_payment_amount_invalid"


def test_payment_integrity_response_fails_closed_if_stop_cannot_persist(monkeypatch):
    error = payment_api.ProviderPaymentIntegrityError("mismatch", "Provider mismatch")

    monkeypatch.setattr(
        payment_api,
        "trip_pilot_circuit_breaker",
        lambda **kwargs: circuit.PilotCircuitTrip(False, False, None),
    )
    normal = payment_api._trip_after_rollback(1, error)
    assert isinstance(normal, HTTPException)
    assert normal.status_code == 409

    def fail(**kwargs):
        raise circuit.PilotCircuitBreakerError("database unavailable")

    monkeypatch.setattr(payment_api, "trip_pilot_circuit_breaker", fail)
    failed = payment_api._trip_after_rollback(1, error)
    assert failed.status_code == 503


def test_reconciliation_mismatch_stops_only_pilot_order():
    _engine, factory = _database()
    db, order = _pilot_order(factory)
    payment = Payment(
        order_id=order.id,
        provider="yookassa",
        provider_payment_id="payment-1",
        status="succeeded",
        amount=100,
        confirmation_url="",
    )
    db.add(payment)
    db.flush()

    row = create_reconciliation_row(db, payment, "canceled", 100)
    db.commit()

    assert row.status == "mismatch"
    assert db.get(PilotRuntimeState, 1).status == "stopped"


def test_matched_reconciliation_does_not_stop_active_pilot():
    _engine, factory = _database()
    db, order = _pilot_order(factory)
    payment = Payment(
        order_id=order.id,
        provider="yookassa",
        provider_payment_id="payment-2",
        status="succeeded",
        amount=100,
        confirmation_url="",
    )
    db.add(payment)
    db.flush()

    row = create_reconciliation_row(db, payment, "succeeded", 100)
    db.commit()

    assert row.status == "matched"
    assert db.get(PilotRuntimeState, 1).status == "active"
