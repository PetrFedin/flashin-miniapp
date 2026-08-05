import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot_control import load_state, new_state, save_state, validate_state  # noqa: E402
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
    assert state["schema_version"] == 3
    assert verify_payload_signature(state, SECRET)
    loaded = load_state(path, expected_admission=BINDING, secret=SECRET)
    assert verify_payload_signature(loaded, SECRET)


def test_tampered_scenario_or_decision_is_rejected(tmp_path: Path):
    path = tmp_path / "live_pilot_state.json"
    _signed_state(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["scenarios"][0]["result"] = "pass"
    payload["decision"] = "GO"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="Pilot control state signature is invalid"):
        load_state(path, expected_admission=BINDING, secret=SECRET)


def test_wrong_secret_and_unsigned_schema_v2_are_rejected(tmp_path: Path):
    path = tmp_path / "live_pilot_state.json"
    _signed_state(path)
    with pytest.raises(ValueError, match="Pilot control state signature is invalid"):
        load_state(path, expected_admission=BINDING, secret="x" * 48)

    path.write_text(json.dumps({"schema_version": 2}), encoding="utf-8")
    with pytest.raises(ValueError, match="Unsigned pilot state schema 2 cannot be reused"):
        load_state(path, secret=SECRET)
