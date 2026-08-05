import json
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
