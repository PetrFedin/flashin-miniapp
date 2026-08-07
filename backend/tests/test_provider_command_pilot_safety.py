from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.database import Base  # noqa: E402
from backend.jobs import provider_command_jobs as jobs  # noqa: E402
from backend.models import Customer, Order  # noqa: E402
from backend.pilot_models import PilotOrderSlot, PilotRuntimeState  # noqa: E402
from backend.provider_models import ProviderCommand  # noqa: E402
from backend.services.provider_command_safety import (  # noqa: E402
    enforce_terminal_provider_command_pilot_stop,
)


def _database():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    return engine, factory


def _pilot_order(factory):
    db = factory()
    customer = Customer(telegram_id="provider-safety-user")
    db.add(customer)
    db.flush()
    order = Order(customer_id=customer.id, total_amount=1000, currency="RUB")
    db.add(order)
    db.flush()
    db.add(
        PilotRuntimeState(
            id=1,
            run_id="pilot-run",
            status="active",
            admission_sha256="a" * 64,
            release_sha256="b" * 64,
            max_orders=20,
            accepted_orders=1,
            allowed_telegram_ids='["provider-safety-user"]',
        )
    )
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


def _command(
    order_id: int,
    *,
    status: str = "pending",
    attempts: int = 0,
    command_type: str = "moysklad.customer_order.create",
    provider: str = "moysklad",
    aggregate_type: str = "order",
) -> ProviderCommand:
    return ProviderCommand(
        provider=provider,
        command_type=command_type,
        idempotency_key=f"provider-safety:{provider}:{order_id}:{command_type}:{status}:{attempts}",
        aggregate_type=aggregate_type,
        aggregate_id=str(order_id),
        payload_json=json.dumps({"order_id": order_id}),
        status=status,
        attempts=attempts,
    )


@pytest.mark.parametrize(
    ("terminal_status", "reason"),
    [
        ("review_required", "auto:provider_command_review_required"),
        ("failed", "auto:provider_command_terminal_failed"),
    ],
)
def test_existing_terminal_moysklad_command_stops_current_pilot(terminal_status, reason):
    _engine, factory = _database()
    db, order = _pilot_order(factory)
    db.add(_command(order.id, status=terminal_status))
    db.commit()

    result = enforce_terminal_provider_command_pilot_stop(db)
    state = db.get(PilotRuntimeState, 1)

    assert result == {
        "current_run": 1,
        "terminal_commands": 1,
        "affected_orders": 1,
        "stopped": 1,
    }
    assert state.status == "stopped"
    assert state.stop_reason == reason

    second = enforce_terminal_provider_command_pilot_stop(db)
    assert second["stopped"] == 0
    assert db.get(PilotRuntimeState, 1).stop_reason == reason


def test_retryable_command_does_not_stop_pilot(monkeypatch):
    _engine, factory = _database()
    db, order = _pilot_order(factory)
    db.add(_command(order.id))
    db.commit()

    async def transient_failure(_db, _payload):
        raise RuntimeError("temporary MoySklad outage")

    monkeypatch.setitem(
        jobs._HANDLERS,
        "moysklad.customer_order.create",
        transient_failure,
    )
    result = asyncio.run(jobs.process_provider_commands(db))
    command = db.query(ProviderCommand).one()

    assert result["retry_scheduled"] == 1
    assert command.status == "pending"
    assert command.attempts == 1
    assert db.get(PilotRuntimeState, 1).status == "active"


def test_tenth_failure_is_terminal_and_stops_pilot(monkeypatch):
    _engine, factory = _database()
    db, order = _pilot_order(factory)
    db.add(_command(order.id, attempts=9))
    db.commit()

    async def terminal_failure(_db, _payload):
        raise RuntimeError("provider still unavailable")

    monkeypatch.setitem(
        jobs._HANDLERS,
        "moysklad.customer_order.create",
        terminal_failure,
    )
    result = asyncio.run(jobs.process_provider_commands(db))
    command = db.query(ProviderCommand).one()
    state = db.get(PilotRuntimeState, 1)

    assert result["failed"] == 1
    assert command.status == "failed"
    assert command.attempts == 10
    assert state.status == "stopped"
    assert state.stop_reason == "auto:provider_command_terminal_failed"


def test_review_required_created_by_worker_stops_pilot_immediately():
    _engine, factory = _database()
    db, order = _pilot_order(factory)
    db.add(
        _command(
            order.id,
            command_type="moysklad.unsupported.create",
        )
    )
    db.commit()

    result = asyncio.run(jobs.process_provider_commands(db))
    command = db.query(ProviderCommand).one()
    state = db.get(PilotRuntimeState, 1)

    assert result["review_required"] == 1
    assert command.status == "review_required"
    assert state.status == "stopped"
    assert state.stop_reason == "auto:provider_command_review_required"


def test_existing_terminal_command_is_reconciled_before_claiming_new_work():
    _engine, factory = _database()
    db, order = _pilot_order(factory)
    db.add(_command(order.id, status="review_required"))
    db.commit()

    result = asyncio.run(jobs.process_provider_commands(db))

    assert result["claimed"] == 0
    assert db.get(PilotRuntimeState, 1).status == "stopped"


def test_historical_nonpilot_and_noncritical_terminal_commands_do_not_stop_current_run():
    _engine, factory = _database()
    db, order = _pilot_order(factory)
    customer_id = order.customer_id

    historical_order = Order(customer_id=customer_id, total_amount=500, currency="RUB")
    db.add(historical_order)
    db.flush()
    db.add(
        PilotOrderSlot(
            run_id="old-pilot-run",
            sequence=1,
            order_id=historical_order.id,
            customer_id=customer_id,
            admission_sha256="c" * 64,
        )
    )
    db.add(_command(historical_order.id, status="failed"))
    db.add(_command(order.id, status="failed", provider="future-provider"))
    db.add(_command(order.id, status="review_required", aggregate_type="return"))
    db.commit()

    result = enforce_terminal_provider_command_pilot_stop(db)

    assert result["terminal_commands"] == 0
    assert result["stopped"] == 0
    assert db.get(PilotRuntimeState, 1).status == "active"
