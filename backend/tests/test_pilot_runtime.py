import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import Customer, Order
from backend.pilot_models import PilotOrderSlot, PilotRuntimeState
from backend.services.pilot_runtime import (
    acquire_pilot_checkout,
    record_pilot_order,
    sha256_file,
)
from scripts.pilot_evidence import configuration_fingerprint, sign_payload


def _runtime(tmp_path: Path, *, accepted_orders: int = 0):
    docs = tmp_path / "docs"
    pilot_docs = docs / "pilot"
    releases = tmp_path / "deploy/release/runtime"
    pilot_docs.mkdir(parents=True)
    releases.mkdir(parents=True)

    env = {
        "PILOT_EVIDENCE_SIGNING_SECRET": "s" * 48,
        "APP_ENV": "production",
        "API_PUBLIC_URL": "https://api.flashin.store",
        "MINI_APP_URL": "https://mini.flashin.store",
        "ADMIN_URL": "https://admin.flashin.store",
    }
    evidence_paths = {
        "provider_report": pilot_docs / "integration_check_report.json",
        "live_gate_report": docs / "pilot_live_gate_report.json",
        "rollback_drill_report": pilot_docs / "rollback_drill_report.json",
    }
    for key, path in evidence_paths.items():
        path.write_text(json.dumps({"kind": key}), encoding="utf-8")

    release_sha = "r" * 64
    release_path = releases / "current_release.json"
    release_path.write_text(json.dumps({"sha256": release_sha}), encoding="utf-8")

    pilot_created_at = "2026-08-03T18:00:00Z"
    pilot_path = pilot_docs / "live_pilot_state.json"
    pilot_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "created_at": pilot_created_at,
                "decision": "NO-GO",
                "scenarios": [{} for _ in range(20)],
            }
        ),
        encoding="utf-8",
    )

    manifest_path = pilot_docs / "pilot_admission_manifest.json"
    manifest = {
        "kind": "pilot_admission",
        "decision": "GO",
        "configuration_fingerprint": configuration_fingerprint(
            env, env["PILOT_EVIDENCE_SIGNING_SECRET"]
        ),
        "release": {"sha256": release_sha},
        "evidence": {
            key: {"path": str(path), "sha256": sha256_file(path)}
            for key, path in evidence_paths.items()
        },
    }
    manifest_path.write_text(
        json.dumps(sign_payload(manifest, env["PILOT_EVIDENCE_SIGNING_SECRET"])),
        encoding="utf-8",
    )

    settings = SimpleNamespace(
        pilot_runtime_enforced=True,
        pilot_runtime_max_orders=20,
        pilot_admission_manifest_path=str(manifest_path),
        pilot_current_release_path=str(release_path),
        pilot_state_path=str(pilot_path),
        pilot_evidence_signing_secret=env["PILOT_EVIDENCE_SIGNING_SECRET"],
    )
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    customer = Customer(telegram_id="123456")
    session.add(customer)
    session.flush()
    state = PilotRuntimeState(
        id=1,
        run_id="pilot-run",
        status="active",
        admission_sha256=sha256_file(manifest_path),
        release_sha256=release_sha,
        pilot_state_created_at=pilot_created_at,
        max_orders=20,
        accepted_orders=accepted_orders,
        allowed_telegram_ids='["123456"]',
    )
    session.add(state)
    for sequence in range(1, accepted_orders + 1):
        order = Order(customer_id=customer.id, total_amount=100, currency="RUB")
        session.add(order)
        session.flush()
        session.add(
            PilotOrderSlot(
                run_id=state.run_id,
                sequence=sequence,
                order_id=order.id,
                customer_id=customer.id,
                admission_sha256=state.admission_sha256,
            )
        )
    session.commit()
    return session, customer, settings, env, pilot_path, manifest_path


def test_allowlisted_checkout_consumes_one_atomic_slot(tmp_path):
    db, customer, settings, env, _pilot_path, _manifest_path = _runtime(tmp_path)

    context = acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    order = Order(customer_id=customer.id, total_amount=100, currency="RUB")
    db.add(order)
    db.flush()
    record_pilot_order(db, context=context, order=order, customer=customer)
    db.commit()

    state = db.get(PilotRuntimeState, 1)
    assert state.accepted_orders == 1
    assert state.status == "active"
    assert db.query(PilotOrderSlot).count() == 1


def test_non_allowlisted_customer_and_stop_decision_are_blocked(tmp_path):
    db, customer, settings, env, pilot_path, _manifest_path = _runtime(tmp_path)
    customer.telegram_id = "999999"
    db.commit()

    with pytest.raises(HTTPException) as outsider:
        acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    assert outsider.value.status_code == 423

    customer.telegram_id = "123456"
    db.commit()
    payload = json.loads(pilot_path.read_text(encoding="utf-8"))
    payload["decision"] = "STOP"
    pilot_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(HTTPException) as stopped:
        acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    assert stopped.value.status_code == 423


def test_tampered_admission_and_counter_drift_fail_closed(tmp_path):
    db, customer, settings, env, _pilot_path, manifest_path = _runtime(tmp_path)
    manifest_path.write_text("{}", encoding="utf-8")
    with pytest.raises(HTTPException) as tampered:
        acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    assert tampered.value.status_code == 503

    db, customer, settings, env, _pilot_path, _manifest_path = _runtime(tmp_path / "drift")
    state = db.get(PilotRuntimeState, 1)
    state.accepted_orders = 1
    db.commit()
    with pytest.raises(HTTPException) as drift:
        acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    assert drift.value.status_code == 503


def test_twentieth_order_closes_runtime_without_exceeding_limit(tmp_path):
    db, customer, settings, env, _pilot_path, _manifest_path = _runtime(
        tmp_path, accepted_orders=19
    )

    context = acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    assert context.sequence == 20
    order = Order(customer_id=customer.id, total_amount=100, currency="RUB")
    db.add(order)
    db.flush()
    record_pilot_order(db, context=context, order=order, customer=customer)
    db.commit()

    state = db.get(PilotRuntimeState, 1)
    assert state.accepted_orders == 20
    assert state.status == "completed"
    with pytest.raises(HTTPException) as over_limit:
        acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    assert over_limit.value.status_code == 423


def test_development_runtime_can_remain_disabled(tmp_path):
    db, customer, settings, env, _pilot_path, _manifest_path = _runtime(tmp_path)
    settings.pilot_runtime_enforced = False
    assert acquire_pilot_checkout(db, customer=customer, settings=settings, env=env) is None
