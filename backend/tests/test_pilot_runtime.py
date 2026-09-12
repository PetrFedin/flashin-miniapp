import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base, utcnow_naive
from backend.models import Customer, Order, OrderItem, Product, ProductVariant
from backend.pilot_models import PilotOrderSlot, PilotRuntimeState
from backend.provider_models import ProviderCommand
from backend.services.inventory import reserve_variant
from backend.services.pilot_runtime import (
    acquire_pilot_checkout,
    assert_pilot_new_payment_attempt_allowed,
    record_pilot_order,
    sha256_file,
)
from scripts.pilot_evidence import configuration_fingerprint, sign_payload
from scripts.pilot_control_audit import build_audit_entry, normalize_mutation
from scripts.pilot_control_binding import build_admission_binding
from scripts.pilot_control_chain import state_anchor
from scripts.pilot_release_contract import CAPABILITY_VERSION


def _capability(state: dict, secret: str) -> dict:
    return sign_payload(
        {
            "schema_version": 1,
            "kind": "release_capability",
            "name": "pilot_runtime_guard",
            "version": CAPABILITY_VERSION,
            "archive_sha256": state["sha256"],
            "git_commit": state["git_commit"],
            "release_id": state["release_id"],
        },
        secret,
    )


def _attach_pending_inventory(db, order: Order) -> None:
    suffix = str(order.id)
    product = Product(
        sku=f"pilot-{suffix}",
        title=f"Pilot Product {suffix}",
        slug=f"pilot-{suffix}",
        price=100,
    )
    db.add(product)
    db.flush()
    variant = ProductVariant(
        product_id=product.id,
        size="M",
        sku=f"pilot-{suffix}-M",
        stock_qty=10,
        reserved_qty=0,
    )
    db.add(variant)
    db.flush()
    db.add(
        OrderItem(
            order_id=order.id,
            product_id=product.id,
            variant_id=variant.id,
            title=product.title,
            size=variant.size,
            quantity=1,
            price=100,
        )
    )
    db.flush()
    reserve_variant(
        db,
        variant.id,
        1,
        order_id=order.id,
        source="pilot-runtime-test",
    )


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
    secret = env["PILOT_EVIDENCE_SIGNING_SECRET"]
    evidence_paths = {
        "provider_report": pilot_docs / "integration_check_report.json",
        "live_gate_report": docs / "pilot_live_gate_report.json",
        "rollback_drill_report": pilot_docs / "rollback_drill_report.json",
    }
    for key, path in evidence_paths.items():
        path.write_text(json.dumps({"kind": key}), encoding="utf-8")

    current = {
        "release_id": "current-release",
        "git_commit": "c" * 40,
        "sha256": "r" * 64,
    }
    current["capabilities"] = {"pilot_runtime_guard": _capability(current, secret)}
    previous = {
        "release_id": "previous-release",
        "git_commit": "p" * 40,
        "sha256": "q" * 64,
    }
    previous["capabilities"] = {"pilot_runtime_guard": _capability(previous, secret)}
    current_path = releases / "current_release.json"
    previous_path = releases / "previous_release.json"
    current_path.write_text(json.dumps(current), encoding="utf-8")
    previous_path.write_text(json.dumps(previous), encoding="utf-8")

    pilot_created_at = "2026-08-03T18:00:00Z"
    pilot_path = pilot_docs / "live_pilot_state.json"
    pilot_payload = {
        "schema_version": 7,
        "database_evidence_contract": 1,
        "inventory_evidence_contract": 1,
        "revision": 1,
        "state_history_sha256": [],
        "created_at": pilot_created_at,
        "decision": "NO-GO",
        "scenarios": [
            {"number": number, "result": "todo"}
            for number in range(1, 21)
        ],
    }

    manifest_path = pilot_docs / "pilot_admission_manifest.json"
    approvals = {
        "business_owner": "Business",
        "operations_owner": "Operations",
        "technical_owner": "Technical",
        "legal_owner": "Legal",
        "support_owner": "Support",
    }
    manifest = {
        "kind": "pilot_admission",
        "created_at": "2026-08-05T12:00:00Z",
        "decision": "GO",
        "configuration_fingerprint": configuration_fingerprint(env, secret),
        "release": current,
        "previous_release": previous,
        "approvals": approvals,
        "evidence": {
            key: {"path": str(path), "sha256": sha256_file(path)}
            for key, path in evidence_paths.items()
        },
    }
    manifest_path.write_text(
        json.dumps(sign_payload(manifest, secret)),
        encoding="utf-8",
    )
    signed_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pilot_payload["admission"] = build_admission_binding(manifest_path, signed_manifest)
    init_mutation = normalize_mutation(
        operation="init",
        operator_role="operations_owner",
        operator_name="Operations",
        reason="Initialize controlled pilot state",
        approvals=approvals,
    )
    pilot_payload["audit_log"] = [
        build_audit_entry(
            init_mutation,
            revision=1,
            parent_state_sha256=None,
            changed_at=pilot_created_at,
        )
    ]
    signed_pilot = sign_payload(pilot_payload, secret)
    pilot_path.write_text(json.dumps(signed_pilot), encoding="utf-8")
    pilot_anchor = state_anchor(signed_pilot)

    settings = SimpleNamespace(
        pilot_runtime_enforced=True,
        pilot_runtime_max_orders=20,
        pilot_admission_manifest_path=str(manifest_path),
        pilot_current_release_path=str(current_path),
        pilot_previous_release_path=str(previous_path),
        pilot_state_path=str(pilot_path),
        pilot_evidence_signing_secret=secret,
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
        release_sha256=current["sha256"],
        pilot_state_created_at=pilot_created_at,
        pilot_state_revision=pilot_anchor["revision"],
        pilot_state_sha256=pilot_anchor["sha256"],
        max_orders=20,
        accepted_orders=accepted_orders,
        allowed_telegram_ids='["123456"]',
        opened_at=utcnow_naive(),
    )
    session.add(state)
    pilot_orders: list[Order] = []
    for sequence in range(1, accepted_orders + 1):
        order = Order(customer_id=customer.id, total_amount=100, currency="RUB")
        session.add(order)
        session.flush()
        _attach_pending_inventory(session, order)
        pilot_orders.append(order)
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

    if pilot_orders:
        current_pilot = json.loads(pilot_path.read_text(encoding="utf-8"))
        for sequence, order in enumerate(pilot_orders, start=1):
            current_pilot = _next_signed_state(
                current_pilot,
                secret,
                scenario_number=sequence,
                result="pass",
                scenario_changes={
                    "order_id": str(order.id),
                    "order_status": str(order.status),
                },
            )
        pilot_path.write_text(json.dumps(current_pilot), encoding="utf-8")

    return session, customer, settings, env, pilot_path, manifest_path, previous_path


def _next_signed_state(
    payload: dict,
    secret: str,
    *,
    scenario_number: int = 1,
    result: str = "running",
    decision: str | None = None,
    admission_sha256: str | None = None,
    parent_sha256: str | None = None,
    scenario_changes: dict | None = None,
) -> dict:
    parent = state_anchor(payload)
    child = json.loads(json.dumps(payload))
    child.pop("signature", None)
    effective_parent = parent_sha256 or parent["sha256"]
    child["revision"] = parent["revision"] + 1
    child["state_history_sha256"] = [*parent["history"], effective_parent]
    record = child["scenarios"][scenario_number - 1]
    record["result"] = result
    for key, value in (scenario_changes or {}).items():
        record[key] = value
    if decision is not None:
        child["decision"] = decision
    if admission_sha256 is not None:
        child["admission"]["manifest_sha256"] = admission_sha256
    approvals = {
        "business_owner": "Business",
        "operations_owner": "Operations",
        "technical_owner": "Technical",
        "legal_owner": "Legal",
        "support_owner": "Support",
    }
    mutation = normalize_mutation(
        operation="record",
        operator_role="operations_owner",
        operator_name="Operations",
        reason=f"Record verified outcome for scenario {scenario_number}",
        approvals=approvals,
        scenario_number=scenario_number,
        result=result,
    )
    child["audit_log"] = [
        *list(payload["audit_log"]),
        build_audit_entry(
            mutation,
            revision=child["revision"],
            parent_state_sha256=effective_parent,
        ),
    ]
    return sign_payload(child, secret)


def test_allowlisted_checkout_consumes_one_atomic_slot(tmp_path):
    db, customer, settings, env, *_ = _runtime(tmp_path)

    context = acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    order = Order(customer_id=customer.id, total_amount=100, currency="RUB")
    db.add(order)
    db.flush()
    _attach_pending_inventory(db, order)
    record_pilot_order(db, context=context, order=order, customer=customer)
    db.commit()

    state = db.get(PilotRuntimeState, 1)
    assert state.accepted_orders == 1
    assert state.status == "active"
    assert db.query(PilotOrderSlot).count() == 1


def test_next_checkout_waits_for_signed_previous_scenario_without_stopping_runtime(tmp_path):
    db, customer, settings, env, pilot_path, *_ = _runtime(tmp_path)

    first = acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    order = Order(customer_id=customer.id, total_amount=100, currency="RUB")
    db.add(order)
    db.flush()
    _attach_pending_inventory(db, order)
    record_pilot_order(db, context=first, order=order, customer=customer)
    db.commit()

    with pytest.raises(HTTPException) as waiting:
        acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    assert waiting.value.status_code == 423
    assert waiting.value.detail == {
        "code": "pilot_checkout_unavailable",
        "message": "Checkout is temporarily unavailable during the controlled pilot.",
    }

    runtime = db.get(PilotRuntimeState, 1)
    assert runtime.status == "active"
    assert not runtime.stop_reason
    assert runtime.accepted_orders == 1

    payload = json.loads(pilot_path.read_text(encoding="utf-8"))
    passed = _next_signed_state(
        payload,
        env["PILOT_EVIDENCE_SIGNING_SECRET"],
        scenario_number=1,
        result="pass",
        scenario_changes={
            "order_id": str(order.id),
            "order_status": str(order.status),
        },
    )
    pilot_path.write_text(json.dumps(passed), encoding="utf-8")

    second = acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    assert second.sequence == 2


def test_current_slot_payment_guard_does_not_wait_for_scenario_pass(tmp_path):
    db, customer, settings, env, *_ = _runtime(tmp_path)

    context = acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    order = Order(customer_id=customer.id, total_amount=100, currency="RUB")
    db.add(order)
    db.flush()
    _attach_pending_inventory(db, order)
    record_pilot_order(db, context=context, order=order, customer=customer)
    db.commit()

    assert_pilot_new_payment_attempt_allowed(
        db,
        order_id=order.id,
        settings=settings,
        env=env,
    )
    runtime = db.get(PilotRuntimeState, 1)
    assert runtime.status == "active"
    assert not runtime.stop_reason


def test_non_allowlisted_customer_and_stop_decision_are_blocked(tmp_path):
    db, customer, settings, env, pilot_path, *_ = _runtime(tmp_path)
    customer.telegram_id = "999999"
    db.commit()

    with pytest.raises(HTTPException) as outsider:
        acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    assert outsider.value.status_code == 423

    customer.telegram_id = "123456"
    db.commit()
    payload = json.loads(pilot_path.read_text(encoding="utf-8"))
    pilot_path.write_text(
        json.dumps(
            _next_signed_state(
                payload,
                env["PILOT_EVIDENCE_SIGNING_SECRET"],
                result="fail",
                decision="STOP",
            )
        ),
        encoding="utf-8",
    )
    with pytest.raises(HTTPException) as stopped:
        acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    assert stopped.value.status_code == 423


def test_tampered_admission_counter_or_rollback_capability_fail_closed(tmp_path):
    db, customer, settings, env, _pilot_path, manifest_path, _previous_path = _runtime(tmp_path)
    manifest_path.write_text("{}", encoding="utf-8")
    with pytest.raises(HTTPException) as tampered:
        acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    assert tampered.value.status_code == 503

    db, customer, settings, env, *_ = _runtime(tmp_path / "drift")
    state = db.get(PilotRuntimeState, 1)
    state.accepted_orders = 1
    db.commit()
    with pytest.raises(HTTPException) as drift:
        acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    assert drift.value.status_code == 503

    db, customer, settings, env, _pilot, _manifest, previous_path = _runtime(tmp_path / "capability")
    previous = json.loads(previous_path.read_text(encoding="utf-8"))
    previous["capabilities"] = {}
    previous_path.write_text(json.dumps(previous), encoding="utf-8")
    with pytest.raises(HTTPException) as unsafe_rollback:
        acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    assert unsafe_rollback.value.status_code == 503


def test_twentieth_order_closes_runtime_without_exceeding_limit(tmp_path):
    db, customer, settings, env, *_ = _runtime(tmp_path, accepted_orders=19)

    context = acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    assert context.sequence == 20
    order = Order(customer_id=customer.id, total_amount=100, currency="RUB")
    db.add(order)
    db.flush()
    _attach_pending_inventory(db, order)
    record_pilot_order(db, context=context, order=order, customer=customer)
    db.commit()

    state = db.get(PilotRuntimeState, 1)
    assert state.accepted_orders == 20
    assert state.status == "completed"
    with pytest.raises(HTTPException) as over_limit:
        acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    assert over_limit.value.status_code == 423


def test_development_runtime_can_remain_disabled(tmp_path):
    db, customer, settings, env, *_ = _runtime(tmp_path)
    settings.pilot_runtime_enforced = False
    assert acquire_pilot_checkout(db, customer=customer, settings=settings, env=env) is None


def test_pilot_state_bound_to_other_admission_fails_closed(tmp_path):
    db, customer, settings, env, pilot_path, *_ = _runtime(tmp_path)
    payload = json.loads(pilot_path.read_text(encoding="utf-8"))
    pilot_path.write_text(
        json.dumps(
            _next_signed_state(
                payload,
                env["PILOT_EVIDENCE_SIGNING_SECRET"],
                admission_sha256="0" * 64,
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(HTTPException) as mismatch:
        acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    assert mismatch.value.status_code == 503


def test_tampered_pilot_control_state_fails_closed_on_checkout(tmp_path):
    db, customer, settings, env, pilot_path, *_ = _runtime(tmp_path)
    payload = json.loads(pilot_path.read_text(encoding="utf-8"))
    payload["scenarios"][0]["result"] = "pass"
    pilot_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(HTTPException) as tampered:
        acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    assert tampered.value.status_code == 503


def test_runtime_anchor_advances_to_descendant_and_rejects_replay(tmp_path):
    db, customer, settings, env, pilot_path, *_ = _runtime(tmp_path)
    original_bytes = pilot_path.read_bytes()
    original = json.loads(original_bytes)
    descendant = _next_signed_state(
        original, env["PILOT_EVIDENCE_SIGNING_SECRET"]
    )
    pilot_path.write_text(json.dumps(descendant), encoding="utf-8")

    acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    db.commit()
    runtime = db.get(PilotRuntimeState, 1)
    child_anchor = state_anchor(descendant)
    assert runtime.pilot_state_revision == child_anchor["revision"]
    assert runtime.pilot_state_sha256 == child_anchor["sha256"]

    pilot_path.write_bytes(original_bytes)
    with pytest.raises(HTTPException) as replay:
        acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    assert replay.value.status_code == 503


def test_unrelated_valid_signed_state_branch_fails_closed(tmp_path):
    db, customer, settings, env, pilot_path, *_ = _runtime(tmp_path)
    current = json.loads(pilot_path.read_text(encoding="utf-8"))
    fork = _next_signed_state(
        current,
        env["PILOT_EVIDENCE_SIGNING_SECRET"],
        parent_sha256="f" * 64,
    )
    pilot_path.write_text(json.dumps(fork), encoding="utf-8")

    with pytest.raises(HTTPException) as unrelated:
        acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    assert unrelated.value.status_code == 503


def test_current_run_provider_failure_blocks_new_checkout_without_leaking_details(tmp_path):
    db, customer, settings, env, *_ = _runtime(tmp_path)
    state = db.get(PilotRuntimeState, 1)
    db.add(
        ProviderCommand(
            provider="moysklad",
            command_type="customer_order.create",
            idempotency_key="private-pilot-command-key",
            aggregate_type="order",
            aggregate_id="private-order-id",
            payload_json='{"secret":"must-not-leak"}',
            status="failed",
            last_error="private provider failure detail",
            created_at=state.opened_at,
        )
    )
    db.commit()

    with pytest.raises(HTTPException) as blocked:
        acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)

    assert blocked.value.status_code == 423
    assert blocked.value.detail == {
        "code": "pilot_checkout_unavailable",
        "message": "Checkout is temporarily unavailable during the controlled pilot.",
    }
    serialized = json.dumps(blocked.value.detail)
    assert "private-pilot-command-key" not in serialized
    assert "must-not-leak" not in serialized
    assert "provider failure" not in serialized


def test_historical_provider_failure_before_runtime_window_does_not_block_checkout(tmp_path):
    db, customer, settings, env, *_ = _runtime(tmp_path)
    state = db.get(PilotRuntimeState, 1)
    db.add(
        ProviderCommand(
            provider="moysklad",
            command_type="customer_order.create",
            idempotency_key="historical-command",
            status="failed",
            created_at=state.opened_at - timedelta(seconds=1),
        )
    )
    db.commit()

    context = acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    assert context is not None
    assert context.sequence == 1


def test_active_runtime_without_opened_at_fails_integrity_check(tmp_path):
    db, customer, settings, env, *_ = _runtime(tmp_path)
    state = db.get(PilotRuntimeState, 1)
    state.opened_at = None
    db.commit()

    with pytest.raises(HTTPException) as invalid:
        acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    assert invalid.value.status_code == 503
    assert invalid.value.detail["code"] == "pilot_runtime_integrity_failure"
