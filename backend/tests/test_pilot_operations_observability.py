import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.api import ops
from backend.database import Base, utcnow_naive
from backend.main import app
from backend.models import Customer, Order, PaymentReconciliation, ReturnRequest
from backend.pilot_models import PilotOrderSlot, PilotRuntimeState
from backend.provider_models import ProviderCommand
from backend.services import pilot_observability, pilot_operational_safety


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
        opened_at=utcnow_naive(),
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


def _mock_verified_runtime(
    monkeypatch,
    *,
    sequence_ready: bool = True,
    database_errors: list[str] | None = None,
    database_exception: Exception | None = None,
):
    def fake_validate_runtime_files(state, _settings, **kwargs):
        target = kwargs.get("validated_pilot_state")
        if target is not None:
            target.update(
                {
                    "scenarios": [
                        {
                            "number": number,
                            "result": (
                                "pass"
                                if number <= state.accepted_orders
                                and (sequence_ready or number < state.accepted_orders)
                                else "running"
                                if number == state.accepted_orders and state.accepted_orders > 0
                                else "todo"
                            ),
                        }
                        for number in range(1, 21)
                    ]
                }
            )
        return []

    def fake_database_evidence(*_args, **_kwargs):
        if database_exception is not None:
            raise database_exception
        return list(database_errors or [])

    def fake_inventory_safety(_db, order_ids):
        return {
            "healthy": True,
            "blocking_codes": [],
            "pilot_orders": len(order_ids),
            "pilot_variants": 0,
            "open_reconciliation_variants": 0,
            "chain_failures": 0,
            "stop_reason": None,
        }

    monkeypatch.setattr(
        pilot_observability,
        "validate_runtime_files",
        fake_validate_runtime_files,
    )
    monkeypatch.setattr(
        pilot_observability,
        "validate_pilot_database_evidence",
        fake_database_evidence,
    )
    monkeypatch.setattr(
        pilot_operational_safety,
        "build_pilot_inventory_safety",
        fake_inventory_safety,
    )


def test_healthy_active_runtime_is_go_without_exposing_allowlist_or_raw_run_id(monkeypatch):
    db = _database()
    _state, _customer, _orders = _active_runtime(db)
    _mock_verified_runtime(monkeypatch)

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
    assert snapshot["continuation"] == {
        "applicable": True,
        "ready": True,
        "next_sequence": 3,
    }
    assert snapshot["operational_safety"]["applicable"] is True
    assert snapshot["operational_safety"]["healthy"] is True
    assert snapshot["operational_safety"]["blocking_codes"] == []
    assert snapshot["operational_safety"]["grace_minutes"] == 15
    serialized = json.dumps(snapshot, ensure_ascii=False)
    assert "123456789" not in serialized
    assert "987654321" not in serialized
    assert "pilot-run-2026" not in serialized
    assert "allowed_telegram_ids" not in serialized
    assert '"run_id"' not in serialized


def test_pending_previous_scenario_blocks_only_next_checkout_without_mutating_runtime(monkeypatch):
    db = _database()
    state, _customer, _orders = _active_runtime(db, accepted_orders=1)
    _mock_verified_runtime(monkeypatch, sequence_ready=False)
    before = (state.status, state.stop_reason, state.accepted_orders, state.updated_at)

    snapshot = pilot_observability.build_pilot_operations_status(db, _settings())

    assert snapshot["checkout_decision"] == "NO-GO"
    assert snapshot["database_integrity"] == {"healthy": True, "codes": []}
    assert snapshot["artifact_integrity"]["healthy"] is True
    assert snapshot["continuation"] == {
        "applicable": True,
        "ready": False,
        "next_sequence": 2,
    }
    db.refresh(state)
    assert (state.status, state.stop_reason, state.accepted_orders, state.updated_at) == before


def test_database_evidence_failure_is_bounded_and_private_details_never_escape(monkeypatch):
    db = _database()
    _active_runtime(db, accepted_orders=1)
    _mock_verified_runtime(
        monkeypatch,
        database_errors=["#1 private order 741 provider=/srv/private/customer-123456789"],
    )

    snapshot = pilot_observability.build_pilot_operations_status(db, _settings())

    assert snapshot["checkout_decision"] == "NO-GO"
    assert snapshot["database_integrity"] == {
        "healthy": False,
        "codes": ["pilot_database_evidence_invalid"],
    }
    assert snapshot["continuation"] == {
        "applicable": True,
        "ready": None,
        "next_sequence": 2,
    }
    serialized = json.dumps(snapshot)
    assert "741" not in serialized
    assert "/srv/private" not in serialized
    assert "123456789" not in serialized


def test_database_evidence_exception_is_same_bounded_no_go(monkeypatch):
    db = _database()
    _active_runtime(db, accepted_orders=1)
    _mock_verified_runtime(
        monkeypatch,
        database_exception=RuntimeError("private database order 123456789"),
    )

    snapshot = pilot_observability.build_pilot_operations_status(db, _settings())

    assert snapshot["checkout_decision"] == "NO-GO"
    assert snapshot["database_integrity"]["codes"] == ["pilot_database_evidence_invalid"]
    assert "123456789" not in json.dumps(snapshot)


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
    _mock_verified_runtime(monkeypatch)

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


def test_current_run_provider_failure_is_no_go_and_redacted(monkeypatch):
    db = _database()
    state, _customer, _orders = _active_runtime(db)
    db.add(
        ProviderCommand(
            provider="moysklad",
            command_type="customer_order.create",
            idempotency_key="private-command-key",
            aggregate_type="order",
            aggregate_id="private-order-id",
            payload_json='{"secret":"private-payload"}',
            status="failed",
            last_error="private provider error",
            created_at=state.opened_at,
        )
    )
    db.commit()
    _mock_verified_runtime(monkeypatch)

    snapshot = pilot_observability.build_pilot_operations_status(db, _settings())

    assert snapshot["checkout_decision"] == "NO-GO"
    safety = snapshot["operational_safety"]
    assert safety["applicable"] is True
    assert safety["healthy"] is False
    assert "moysklad_command_terminal_failure" in safety["blocking_codes"]
    assert safety["queues"]["moysklad_commands"]["terminal"] == 1
    serialized = json.dumps(snapshot)
    assert "private-command-key" not in serialized
    assert "private-order-id" not in serialized
    assert "private-payload" not in serialized
    assert "private provider error" not in serialized


def test_historical_provider_failure_before_opened_at_is_ignored(monkeypatch):
    db = _database()
    state, _customer, _orders = _active_runtime(db)
    db.add(
        ProviderCommand(
            provider="moysklad",
            command_type="customer_order.create",
            idempotency_key="old-command-key",
            status="failed",
            created_at=state.opened_at - timedelta(seconds=1),
        )
    )
    db.commit()
    _mock_verified_runtime(monkeypatch)

    snapshot = pilot_observability.build_pilot_operations_status(db, _settings())

    assert snapshot["checkout_decision"] == "GO"
    assert snapshot["operational_safety"]["healthy"] is True
    assert snapshot["operational_safety"]["blocking_codes"] == []


def test_database_counter_or_sequence_drift_is_reported(monkeypatch):
    db = _database()
    state, _customer, _orders = _active_runtime(db, accepted_orders=2)
    slot = db.query(PilotOrderSlot).filter(PilotOrderSlot.sequence == 2).first()
    db.delete(slot)
    state.accepted_orders = 2
    db.commit()
    _mock_verified_runtime(monkeypatch)

    snapshot = pilot_observability.build_pilot_operations_status(db, _settings())

    assert snapshot["checkout_decision"] == "NO-GO"
    assert snapshot["database_integrity"]["healthy"] is False
    assert "slot_count_mismatch" in snapshot["database_integrity"]["codes"]
    assert "slot_sequence_gap" in snapshot["database_integrity"]["codes"]


def test_active_runtime_without_opened_at_is_no_go(monkeypatch):
    db = _database()
    state, _customer, _orders = _active_runtime(db)
    state.opened_at = None
    db.commit()
    _mock_verified_runtime(monkeypatch)

    snapshot = pilot_observability.build_pilot_operations_status(db, _settings())

    assert snapshot["checkout_decision"] == "NO-GO"
    assert "active_runtime_opened_at_missing" in snapshot["database_integrity"]["codes"]
    assert snapshot["operational_safety"]["applicable"] is False
    assert snapshot["operational_safety"]["healthy"] is False


def test_configured_limit_mismatch_is_no_go(monkeypatch):
    db = _database()
    _active_runtime(db)
    _mock_verified_runtime(monkeypatch)

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
    assert snapshot["continuation"]["ready"] is None
    serialized = json.dumps(snapshot)
    assert "/srv/private" not in serialized
    assert "PILOT_EVIDENCE_SIGNING_SECRET" not in serialized


def test_stop_reason_is_reduced_to_an_allowlisted_category(monkeypatch):
    db = _database()
    state, _customer, _orders = _active_runtime(db)
    state.status = "stopped"
    state.stop_reason = "Call customer 123456789 about secret provider incident"
    db.commit()
    _mock_verified_runtime(monkeypatch)

    snapshot = pilot_observability.build_pilot_operations_status(db, _settings())

    assert snapshot["runtime"]["stop_reason"] == "operator_stop"
    assert snapshot["continuation"] == {
        "applicable": False,
        "ready": None,
        "next_sequence": None,
    }
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
    assert snapshot["continuation"] == {
        "applicable": False,
        "ready": None,
        "next_sequence": None,
    }
    assert snapshot["operational_safety"] == {
        "applicable": False,
        "healthy": None,
        "blocking_codes": [],
        "grace_minutes": 15,
        "scope_started_at": None,
        "queues": {},
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


def _walk_routes(routes):
    for route in routes:
        nested = getattr(route, "routes", None)
        if nested is not None:
            yield from _walk_routes(nested)
        else:
            yield route


def test_pilot_operations_route_is_registered_once():
    matching = [
        route
        for route in _walk_routes(app.routes)
        if getattr(route, "name", None) == "pilot_runtime_status"
        and "GET" in getattr(route, "methods", set())
    ]
    assert len(matching) == 1
    assert str(app.url_path_for("pilot_runtime_status")) == "/api/ops/pilot-runtime"
    assert "get" in app.openapi()["paths"]["/api/ops/pilot-runtime"]
