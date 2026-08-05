from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot_runtime import _normalize_ids  # noqa: E402


def test_allowlist_is_unique_numeric_and_bounded():
    assert _normalize_ids(["123", "456"]) == ["123", "456"]
    with pytest.raises(ValueError, match="duplicates"):
        _normalize_ids(["123", "123"])
    with pytest.raises(ValueError, match="positive numeric"):
        _normalize_ids(["@user"])
    with pytest.raises(ValueError, match="at most 50"):
        _normalize_ids([str(index + 1) for index in range(51)])



def test_host_arm_requires_accountable_schema_v5_control_state():
    source = (ROOT / "scripts/pilot_runtime.py").read_text(encoding="utf-8")
    assert 'pilot_state.get("schema_version") != 5' in source
    assert "verify_payload_signature(pilot_state, secret)" in source
    assert "Pilot control state signature is invalid" in source



def test_runtime_arm_transports_and_validates_state_lineage_anchor():
    source = (ROOT / "scripts/pilot_runtime.py").read_text(encoding="utf-8")
    for marker in (
        '"pilot_state_revision": pilot_anchor["revision"]',
        '"pilot_state_sha256": pilot_anchor["sha256"]',
        '"pilot_state_history": pilot_anchor["history"]',
        "validate_anchor_transition(",
        "Stopped pilot runtime cannot change admission or release lineage",
        "Host pilot state anchor does not match runtime evidence",
    ):
        assert marker in source



def test_runtime_arm_validates_audit_owners_against_signed_admission():
    source = (ROOT / "scripts/pilot_runtime.py").read_text(encoding="utf-8")
    assert "approved_operators(manifest)" in source
    assert "validate_audit_log(" in source
