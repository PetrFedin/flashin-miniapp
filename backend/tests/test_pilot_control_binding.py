import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot_control import load_state, new_state  # noqa: E402
from pilot_control_binding import (  # noqa: E402
    build_admission_binding,
    require_admission_binding,
    validate_admission_binding,
)


def _manifest(path: Path) -> dict:
    manifest = {
        "created_at": "2026-08-05T12:00:00Z",
        "configuration_fingerprint": "f" * 64,
        "release": {
            "release_id": "release-a",
            "git_commit": "a" * 40,
            "sha256": "b" * 64,
        },
    }
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def test_state_is_bound_to_one_exact_signed_admission_file(tmp_path):
    path = tmp_path / "pilot_admission_manifest.json"
    binding = build_admission_binding(path, _manifest(path))
    state = new_state(binding)
    assert validate_admission_binding(state, binding) == []

    changed = dict(binding)
    changed["manifest_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="not bound to the current signed admission"):
        require_admission_binding(state, changed)


def test_legacy_state_is_rejected_without_silent_migration(tmp_path):
    state_path = tmp_path / "live_pilot_state.json"
    state_path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="Legacy pilot state schema 1 cannot be reused"):
        load_state(state_path)


def test_makefile_routes_pilot_control_through_admission_runner():
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "python3 scripts/pilot_control.py record" not in makefile
    assert "python3 scripts/pilot_control.py status" not in makefile
    assert "python3 scripts/pilot_control.py validate --final" not in makefile
    assert "python3 scripts/pilot_runner.py record $(ARGS)" in makefile
    assert "python3 scripts/pilot_runner.py status" in makefile
    assert "python3 scripts/pilot_runner.py validate --final" in makefile


def test_runner_revalidates_even_when_state_exists():
    source = (ROOT / "scripts/pilot_runner.py").read_text(encoding="utf-8")
    assert "errors = verify_default_admission(ROOT)" in source
    assert "return pilot_control_main(args)" in source
    assert "if not STATE_PATH.exists()" not in source
