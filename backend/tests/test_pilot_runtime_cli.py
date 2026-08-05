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



def test_host_arm_requires_signed_schema_v3_control_state():
    source = (ROOT / "scripts/pilot_runtime.py").read_text(encoding="utf-8")
    assert 'pilot_state.get("schema_version") != 3' in source
    assert "verify_payload_signature(pilot_state, secret)" in source
    assert "Pilot control state signature is invalid" in source
