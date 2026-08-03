import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.api import ops
from backend.database import Base
from backend.main import app
from backend.models import Customer, Order, PaymentReconciliation, ReturnRequest
from backend.pilot_models import PilotOrderSlot, PilotRuntimeState
from backend.services import pilot_observability


def _database():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _settings(*, enforced: bool = True, max_orders: int = 20):
    return SimpleNamespace(
        pilot_runtime_enforced=enforced,
        pilot_runtime_max_orders=max_orders,
    )


def _active_runtime(db, *, accepted_orders: int = 2):
    customer = Customer(telegram_id="123456789")
    db.add(customer)
    db.flush()
    state = PilotRuntimeState(
        id=1,
        run_id="pilot-run-2026",
        status="active",
        admission_sha256="a" * 64,
        release_sha256="b" * 64,
        pilot_state_created_at="2026-08-03T23:00:00Z",
        max_orders=20,
        accepted_orders=accepted_orders,
        allowed_telegram_ids='["123456789", "987654321"]',
    )
    db.add(state)
    orders = []
    for sequence in range(1, accepted_orders + 1):
        order = Order(
            customer_id=customer.id,
            status="created",
            payment_status="pending",
            total_amount=1000,
            currency="RUB",
        )
        db.add(order)
        db.flush()
        db.add(
            PilotOrderSlot(
                run_id=state.run_id,
                sequence=sequence,
                order_id=order.id,
                customer_id=customer.id,
                admission_sha256=state.admission_sha256,
            )
        )
        orders.append(order)
    db.commit()
    return state, customer, orders


def test_healthy_active_runtime_is_go_without_exposing_allowlist_or_raw_run_id(monkeypatch):
    db = _database()
    _state, _customer, _orders = _active_runtime(db)
    monkeypatch.setattr(pilot_observability, "validate_runtime_files", lambda *_args, **_kwargs: [])

    snapshot = pilot_observability.build_pilot_operations_status(db, _settings())

    assert snapshot["checkout_decision"] == "GO"
    assert snapshot["runtime"]["accepted_orders"] == 2
    assert snapshot["runtime"]["remaining_orders"] == 18
    assert snapshot["runtime"]["slot_count"] == 2
    assert snapshot["runtime"]["allowlist_count"] == 2
    assert len(snapshot["runtime"]["run_ref"]) == 12
    assert snapshot["database_integrity"] == {"healthy": True, "codes": []}
    assert snapshot["artifact_integrity"] == {
        "applicable": True,
        "healthy": True,
        "codes": [],
    }
    serialized = json.dumps(snapshot, ensure_ascii=False)
    assert "123456789" not in serialized
    assert "987654321" not in serialized
    assert "pilot-run-2026" not in serialized
    assert "allowed_telegram_ids" not in serialized
    assert '"run_id"' not in serialized
    assert "telegram" not in serialized.lower()


def test_money_review_signals_force_no_go_without_order_ids(monkeypatch):
    db = _database()
    _state, _customer, orders = _active_runtime(db)
    orders[0].status = "payment_review_required"
    orders[0].payment_status = "paid_review_required"
    db.add(
        ReturnRequest(
            order_id=orders[1].id,
            customer_id=orders[1].customer_id,
            reason="provider refund needs review",
            status="refund_review_required",
        )
    )
    db.add(
        PaymentReconciliation(
            order_id=orders[1].id,
            provider_payment_id="provider-payment-private",
            status="mismatch",
        )
    )
    db.commit()
    monkeypatch.setattr(pilot_observability, "validate_runtime_files", lambda *_args, **_kwargs: [])

    snapshot = pilot_observability.build_pilot_operations_status(db, _settings())

    assert snapshot["checkout_decision"] == "NO-GO"
    assert snapshot["money_attention"] == {
        "payment_review_orders": 1,
        "refund_attention_orders": 1,
        "reconciliation_mismatches": 1,
        "attention_required": True,
    }
    serialized = json.dumps(snapshot)
    assert "provider-payment-private" not in serialized
    assert f'"order_id": {orders[0].id}' not in serialized


def test_database_counter_or_sequence_drift_is_reported(monkeypatch):
    db = _database()
    state, _customer, _orders = _active_runtime(db, accepted_orders=2)
    slot = db.query(PilotOrderSlot).filter(PilotOrderSlot.sequence == 2).first()
    db.delete(slot)
    state.accepted_orders = 2
    db.commit()
    monkeypatch.setattr(pilot_observability, "validate_runtime_files", lambda *_args, **_kwargs: [])

    snapshot = pilot_observability.build_pilot_operations_status(db, _settings())

    assert snapshot["checkout_decision"] == "NO-GO"
    assert snapshot["database_integrity"]["healthy"] is False
    assert "slot_count_mismatch" in snapshot["database_integrity"]["codes"]
    assert "slot_sequence_gap" in snapshot["database_integrity"]["codes"]


def test_configured_limit_mismatch_is_no_go(monkeypatch):
    db = _database()
    _active_runtime(db)
    monkeypatch.setattr(pilot_observability, "validate_runtime_files", lambda *_args, **_kwargs: [])

    snapshot = pilot_observability.build_pilot_operations_status(
        db,
        _settings(max_orders=19),
    )

    assert snapshot["checkout_decision"] == "NO-GO"
    assert "configured_max_orders_not_twenty" in snapshot["database_integrity"]["codes"]
    assert "runtime_config_max_orders_mismatch" in snapshot["database_integrity"]["codes"]


def test_artifact_failures_are_reduced_to_safe_machine_codes(monkeypatch):
    db = _database()
    _active_runtime(db)
    monkeypatch.setattr(
        pilot_observability,
        "validate_runtime_files",
        lambda *_args, **_kwargs: [
            "current release pointer is invalid: /srv/private/release.json",
            "pilot evidence file is missing: /srv/private/provider.json",
            "PILOT_EVIDENCE_SIGNING_SECRET is missing",
        ],
    )

    snapshot = pilot_observability.build_pilot_operations_status(db, _settings())

    assert snapshot["checkout_decision"] == "NO-GO"
    assert snapshot["artifact_integrity"]["healthy"] is False
    assert snapshot["artifact_integrity"]["codes"] == [
        "current_release_invalid",
        "evidence_file_invalid",
        "signing_configuration_invalid",
    ]
    serialized = json.dumps(snapshot)
    assert "/srv/private" not in serialized
    assert "PILOT_EVIDENCE_SIGNING_SECRET" not in serialized


def test_stop_reason_is_reduced_to_an_allowlisted_category(monkeypatch):
    db = _database()
    state, _customer, _orders = _active_runtime(db)
    state.status = "stopped"
    state.stop_reason = "Call customer 123456789 about secret provider incident"
    db.commit()
    monkeypatch.setattr(pilot_observability, "validate_runtime_files", lambda *_args, **_kwargs: [])

    snapshot = pilot_observability.build_pilot_operations_status(db, _settings())

    assert snapshot["runtime"]["stop_reason"] == "operator_stop"
    serialized = json.dumps(snapshot)
    assert "123456789" not in serialized
    assert "secret provider incident" not in serialized

    state.stop_reason = "auto:provider_payment_amount_or_currency_mismatch"
    db.commit()
    snapshot = pilot_observability.build_pilot_operations_status(db, _settings())
    assert snapshot["runtime"]["stop_reason"] == (
        "auto:provider_payment_amount_or_currency_mismatch"
    )

    state.stop_reason = "auto:payment_review:provider_cancel_conflict:customer_123456789"
    db.commit()
    snapshot = pilot_observability.build_pilot_operations_status(db, _settings())
    assert snapshot["runtime"]["stop_reason"] == (
        "auto:payment_review:provider_cancel_conflict"
    )
    assert "123456789" not in json.dumps(snapshot)

    state.stop_reason = "auto:unknown_customer_123456789"
    db.commit()
    snapshot = pilot_observability.build_pilot_operations_status(db, _settings())
    assert snapshot["runtime"]["stop_reason"] == "auto:integrity_failure"
    assert "123456789" not in json.dumps(snapshot)


def test_missing_runtime_is_no_go_when_enforcement_is_enabled():
    db = _database()

    snapshot = pilot_observability.build_pilot_operations_status(db, _settings(enforced=True))

    assert snapshot["runtime"]["present"] is False
    assert snapshot["runtime"]["run_ref"] is None
    assert snapshot["checkout_decision"] == "NO-GO"
    assert snapshot["database_integrity"] == {
        "healthy": False,
        "codes": ["runtime_state_missing"],
    }


def test_ops_endpoint_requires_security_read_and_disables_caching(monkeypatch):
    db = _database()
    monkeypatch.setattr(ops, "get_settings", lambda: _settings())
    monkeypatch.setattr(
        ops,
        "build_pilot_operations_status",
        lambda *_args, **_kwargs: {"checkout_decision": "NO-GO"},
    )
    response = Response()

    result = ops.pilot_runtime_status(
        response=response,
        admin=SimpleNamespace(role="owner"),
        db=db,
    )

    assert result == {"checkout_decision": "NO-GO"}
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"

    with pytest.raises(HTTPException) as forbidden:
        ops.pilot_runtime_status(
            response=Response(),
            admin=SimpleNamespace(role="support"),
            db=db,
        )
    assert forbidden.value.status_code == 403


def test_pilot_operations_route_is_registered_once():
    matching = [
        route
        for route in app.routes
        if getattr(route, "path", None) == "/api/ops/pilot-runtime"
        and "GET" in getattr(route, "methods", set())
    ]
    assert len(matching) == 1
