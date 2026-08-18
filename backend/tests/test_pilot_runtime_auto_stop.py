from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from backend.services import pilot_runtime


class _Db:
    def __init__(self, *, fail_commit=False):
        self.commits = 0
        self.rollbacks = 0
        self.fail_commit = fail_commit

    def commit(self):
        self.commits += 1
        if self.fail_commit:
            raise SQLAlchemyError("commit failed")

    def rollback(self):
        self.rollbacks += 1


def _state(status="active"):
    return SimpleNamespace(
        status=status,
        run_id="pilot-run",
        opened_at=datetime(2026, 8, 17, 20, 0, 0),
        stopped_at=None,
        stop_reason=None,
        updated_at=None,
    )


def _valid_runtime_files(
    state,
    settings,
    *,
    env=None,
    validated_anchor=None,
    validated_pilot_state=None,
):
    if validated_anchor is not None:
        validated_anchor.update({"revision": 2, "sha256": "a" * 64})
    if validated_pilot_state is not None:
        validated_pilot_state.update({"schema_version": 7})
    return []


def _install_common_green_dependencies(monkeypatch):
    monkeypatch.setattr(pilot_runtime, "validate_runtime_files", _valid_runtime_files)
    monkeypatch.setattr(pilot_runtime, "_pilot_order_ids", lambda db, state: [101])
    monkeypatch.setattr(
        pilot_runtime,
        "validate_pilot_database_evidence",
        lambda db, pilot_state, state, final=False: [],
    )
    monkeypatch.setattr(
        pilot_runtime,
        "build_pilot_operational_safety",
        lambda db, created_since: {"healthy": True, "blocking_codes": []},
    )


def test_money_anomaly_persists_stop_before_blocking_new_checkout(monkeypatch):
    _install_common_green_dependencies(monkeypatch)
    monkeypatch.setattr(
        pilot_runtime,
        "build_pilot_money_safety",
        lambda db, order_ids: {
            "healthy": False,
            "attention_required": True,
            "stop_reason": "payment_reconciliation_mismatch",
        },
    )
    db = _Db()
    state = _state()

    with pytest.raises(HTTPException) as blocked:
        pilot_runtime._verify_runtime_safety(
            db,
            state,
            SimpleNamespace(),
            env={},
            blocked_error=pilot_runtime._blocked,
            integrity_error=pilot_runtime._integrity_failure,
        )

    assert blocked.value.status_code == 423
    assert blocked.value.detail["code"] == "pilot_checkout_unavailable"
    assert state.status == "stopped"
    assert state.stop_reason == "auto:payment_reconciliation_mismatch"
    assert state.stopped_at is not None
    assert db.commits == 1
    assert db.rollbacks == 0


def test_operational_anomaly_persists_bounded_stop_reason(monkeypatch):
    _install_common_green_dependencies(monkeypatch)
    monkeypatch.setattr(
        pilot_runtime,
        "build_pilot_money_safety",
        lambda db, order_ids: {"healthy": True, "attention_required": False, "stop_reason": None},
    )
    monkeypatch.setattr(
        pilot_runtime,
        "build_pilot_operational_safety",
        lambda db, created_since: {
            "healthy": False,
            "blocking_codes": ["private-provider-payload-must-not-leak"],
        },
    )
    db = _Db()
    state = _state()

    with pytest.raises(HTTPException) as blocked:
        pilot_runtime._verify_runtime_safety(
            db,
            state,
            SimpleNamespace(),
            env={},
            blocked_error=pilot_runtime._blocked,
            integrity_error=pilot_runtime._integrity_failure,
        )

    assert blocked.value.status_code == 423
    assert state.status == "stopped"
    assert state.stop_reason == "auto:operational_safety_failure"
    assert "private-provider" not in state.stop_reason
    assert "private-provider" not in repr(blocked.value.detail)
    assert db.commits == 1


def test_green_runtime_safety_does_not_commit_or_stop(monkeypatch):
    _install_common_green_dependencies(monkeypatch)
    monkeypatch.setattr(
        pilot_runtime,
        "build_pilot_money_safety",
        lambda db, order_ids: {"healthy": True, "attention_required": False, "stop_reason": None},
    )
    db = _Db()
    state = _state()

    anchor = pilot_runtime._verify_runtime_safety(
        db,
        state,
        SimpleNamespace(),
        env={},
        blocked_error=pilot_runtime._blocked,
        integrity_error=pilot_runtime._integrity_failure,
    )

    assert anchor == {"revision": 2, "sha256": "a" * 64}
    assert state.status == "active"
    assert state.stop_reason is None
    assert db.commits == 0


def test_auto_stop_commit_failure_fails_closed_as_integrity_error():
    db = _Db(fail_commit=True)
    state = _state()

    with pytest.raises(HTTPException) as failed:
        pilot_runtime._persist_auto_stop_and_raise(
            db,
            state,
            reason="pilot_database_integrity_failure",
            response_error=pilot_runtime._blocked,
            integrity_error=pilot_runtime._integrity_failure,
        )

    assert failed.value.status_code == 503
    assert failed.value.detail["code"] == "pilot_runtime_integrity_failure"
    assert db.commits == 1
    assert db.rollbacks == 1


def test_completed_runtime_can_be_circuit_broken_before_fresh_payment():
    db = _Db()
    state = _state(status="completed")

    with pytest.raises(HTTPException) as blocked:
        pilot_runtime._persist_auto_stop_and_raise(
            db,
            state,
            reason="refund_review_required",
            response_error=pilot_runtime._payment_blocked,
            integrity_error=pilot_runtime._payment_integrity_failure,
        )

    assert blocked.value.status_code == 423
    assert blocked.value.detail["code"] == "pilot_payment_attempt_unavailable"
    assert state.status == "stopped"
    assert state.stop_reason == "auto:refund_review_required"
    assert db.commits == 1
