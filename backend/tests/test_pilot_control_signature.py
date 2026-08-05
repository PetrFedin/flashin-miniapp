import json
import multiprocessing
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot_control import load_state, new_state, save_state, validate_state  # noqa: E402
from pilot_control_chain import (  # noqa: E402
    state_anchor,
    validate_anchor_transition,
)
from pilot_evidence import verify_payload_signature  # noqa: E402
from pilot_control_lock import exclusive_state_lock  # noqa: E402

SECRET = "s" * 48
BINDING = {
    "manifest_sha256": "a" * 64,
    "created_at": "2026-08-05T12:00:00Z",
    "configuration_fingerprint": "b" * 64,
    "release": {
        "release_id": "release-a",
        "git_commit": "c" * 40,
        "sha256": "d" * 64,
    },
}


def _concurrent_writer(
    path_text: str,
    scenario_index: int,
    barrier,
    results,
) -> None:
    path = Path(path_text)
    try:
        state = load_state(path, expected_admission=BINDING, secret=SECRET)
        state["scenarios"][scenario_index]["result"] = "running"
        barrier.wait(timeout=10)
        save_state(path, state, validate_state(state, final=False), secret=SECRET)
        results.put(("ok", scenario_index))
    except BaseException as exc:  # process boundary must report every failure
        results.put(("error", str(exc)))


def _hold_state_lock(path_text: str, ready, release) -> None:
    try:
        with exclusive_state_lock(Path(path_text), timeout_seconds=5):
            ready.set()
            release.wait(timeout=10)
    finally:
        ready.set()


def _signed_state(path: Path) -> dict:
    state = new_state(BINDING)
    save_state(path, state, validate_state(state, final=False), secret=SECRET)
    return state


def test_state_write_is_signed_and_exact_state_loads(tmp_path: Path):
    path = tmp_path / "live_pilot_state.json"
    state = _signed_state(path)
    assert state["schema_version"] == 4
    assert state["revision"] == 1
    assert state["state_history_sha256"] == []
    assert verify_payload_signature(state, SECRET)
    loaded = load_state(path, expected_admission=BINDING, secret=SECRET)
    assert verify_payload_signature(loaded, SECRET)


def test_authorized_write_advances_revision_and_preserves_exact_parent_hash(tmp_path: Path):
    path = tmp_path / "live_pilot_state.json"
    state = _signed_state(path)
    parent = state_anchor(state)
    state["scenarios"][0]["result"] = "running"
    save_state(path, state, validate_state(state, final=False), secret=SECRET)
    child = state_anchor(state)

    assert child["revision"] == 2
    assert child["history"] == [parent["sha256"]]
    assert validate_anchor_transition(
        revision=child["revision"],
        sha256=child["sha256"],
        history=child["history"],
        anchored_revision=parent["revision"],
        anchored_sha256=parent["sha256"],
    ) == []


def test_replayed_or_unrelated_signed_branch_is_rejected_by_anchor(tmp_path: Path):
    path = tmp_path / "live_pilot_state.json"
    parent_state = _signed_state(path)
    parent = state_anchor(parent_state)
    parent_state["scenarios"][0]["result"] = "running"
    save_state(path, parent_state, validate_state(parent_state, final=False), secret=SECRET)
    child = state_anchor(parent_state)

    replay_errors = validate_anchor_transition(
        revision=parent["revision"],
        sha256=parent["sha256"],
        history=parent["history"],
        anchored_revision=child["revision"],
        anchored_sha256=child["sha256"],
    )
    assert any("rollback" in item for item in replay_errors)

    fork_errors = validate_anchor_transition(
        revision=2,
        sha256="f" * 64,
        history=["e" * 64],
        anchored_revision=parent["revision"],
        anchored_sha256=parent["sha256"],
    )
    assert any("ancestry" in item for item in fork_errors)


def test_tampered_scenario_or_decision_is_rejected(tmp_path: Path):
    path = tmp_path / "live_pilot_state.json"
    _signed_state(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["scenarios"][0]["result"] = "pass"
    payload["decision"] = "GO"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="Pilot control state signature is invalid"):
        load_state(path, expected_admission=BINDING, secret=SECRET)


def test_wrong_secret_and_legacy_schemas_are_rejected(tmp_path: Path):
    path = tmp_path / "live_pilot_state.json"
    _signed_state(path)
    with pytest.raises(ValueError, match="Pilot control state signature is invalid"):
        load_state(path, expected_admission=BINDING, secret="x" * 48)

    path.write_text(json.dumps({"schema_version": 2}), encoding="utf-8")
    with pytest.raises(ValueError, match="Unsigned pilot state schema 2 cannot be reused"):
        load_state(path, secret=SECRET)

    path.write_text(json.dumps({"schema_version": 3}), encoding="utf-8")
    with pytest.raises(ValueError, match="Replay-vulnerable pilot state schema 3 cannot be reused"):
        load_state(path, secret=SECRET)



def test_concurrent_parent_replacement_is_rejected(tmp_path: Path):
    path = tmp_path / "live_pilot_state.json"
    stale = _signed_state(path)
    current = json.loads(path.read_text(encoding="utf-8"))
    current["scenarios"][1]["result"] = "running"
    save_state(path, current, validate_state(current, final=False), secret=SECRET)

    stale["scenarios"][0]["result"] = "running"
    with pytest.raises(ValueError, match="changed concurrently"):
        save_state(path, stale, validate_state(stale, final=False), secret=SECRET)



def test_cross_process_writers_serialize_and_reject_stale_parent(tmp_path: Path):
    path = tmp_path / "live_pilot_state.json"
    _signed_state(path)
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    results = context.Queue()
    processes = [
        context.Process(
            target=_concurrent_writer,
            args=(str(path), scenario_index, barrier, results),
        )
        for scenario_index in (0, 1)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    outcomes = [results.get(timeout=3) for _ in processes]
    assert sum(kind == "ok" for kind, _ in outcomes) == 1
    errors = [detail for kind, detail in outcomes if kind == "error"]
    assert len(errors) == 1
    assert "changed concurrently" in errors[0]

    final_state = load_state(path, expected_admission=BINDING, secret=SECRET)
    assert final_state["revision"] == 2
    running = [
        item["number"]
        for item in final_state["scenarios"]
        if item["result"] == "running"
    ]
    assert len(running) == 1


def test_cross_process_lock_timeout_fails_closed(tmp_path: Path):
    path = tmp_path / "live_pilot_state.json"
    context = multiprocessing.get_context("fork")
    ready = context.Event()
    release = context.Event()
    holder = context.Process(target=_hold_state_lock, args=(str(path), ready, release))
    holder.start()
    assert ready.wait(timeout=5)
    try:
        with pytest.raises(ValueError, match="lock acquisition timed out"):
            with exclusive_state_lock(path, timeout_seconds=0.1):
                pass
    finally:
        release.set()
        holder.join(timeout=10)
    assert holder.exitcode == 0
