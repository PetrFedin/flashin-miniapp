from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one occurrence, found {count}: {old[:120]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Shared immutable capability version.
replace_once(
    "scripts/pilot_release_contract.py",
    "CAPABILITY_VERSION = 9",
    "CAPABILITY_VERSION = 10",
)

# A small dependency-free binding module works in both script and package import modes.
Path("scripts/pilot_control_binding.py").write_text(
    '''"""Bind a controlled pilot state to one exact signed admission manifest."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

RELEASE_KEYS = ("release_id", "git_commit", "sha256")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_admission_binding(
    manifest_path: Path,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    release = manifest.get("release")
    if not isinstance(release, Mapping):
        raise ValueError("Pilot admission release binding is missing")
    created_at = str(manifest.get("created_at", "")).strip()
    fingerprint = str(manifest.get("configuration_fingerprint", "")).strip()
    normalized_release = {key: str(release.get(key, "")).strip() for key in RELEASE_KEYS}
    if not created_at:
        raise ValueError("Pilot admission created_at is missing")
    if len(fingerprint) != 64:
        raise ValueError("Pilot admission configuration fingerprint is invalid")
    if any(not normalized_release[key] for key in RELEASE_KEYS):
        raise ValueError("Pilot admission release binding is incomplete")
    if len(normalized_release["sha256"]) != 64:
        raise ValueError("Pilot admission release SHA-256 is invalid")
    return {
        "manifest_sha256": sha256_file(manifest_path),
        "created_at": created_at,
        "configuration_fingerprint": fingerprint,
        "release": normalized_release,
    }


def validate_admission_binding(
    state: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> list[str]:
    actual = state.get("admission")
    if not isinstance(actual, Mapping):
        return ["pilot control admission binding is missing"]
    errors: list[str] = []
    for key in ("manifest_sha256", "created_at", "configuration_fingerprint"):
        if actual.get(key) != expected.get(key):
            errors.append(f"pilot control admission {key} does not match current admission")
    actual_release = actual.get("release")
    expected_release = expected.get("release")
    if not isinstance(actual_release, Mapping):
        errors.append("pilot control admission release binding is missing")
    elif not isinstance(expected_release, Mapping):
        errors.append("current admission release binding is missing")
    else:
        for key in RELEASE_KEYS:
            if actual_release.get(key) != expected_release.get(key):
                errors.append(f"pilot control admission release {key} does not match")
    return list(dict.fromkeys(errors))


def require_admission_binding(
    state: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> None:
    errors = validate_admission_binding(state, expected)
    if errors:
        raise ValueError(
            "Pilot control state is not bound to the current signed admission: "
            + "; ".join(errors)
            + ". Archive the old state and initialize a fresh pilot after admission."
        )
''',
    encoding="utf-8",
)

# Pilot control state schema v2 and fail-closed admission verification for every command.
path = Path("scripts/pilot_control.py")
text = path.read_text(encoding="utf-8")
text = text.replace("from typing import Any, Iterable", "from typing import Any, Iterable, Mapping", 1)
text = text.replace(
    "from script_time import utc_timestamp\n\nSCHEMA_VERSION = 1\nDEFAULT_STATE_PATH = Path(\"docs/pilot/live_pilot_state.json\")",
    "from pilot_control_binding import build_admission_binding, require_admission_binding\n"
    "from script_time import utc_timestamp\n\n"
    "ROOT = Path(__file__).resolve().parents[1]\n"
    "SCHEMA_VERSION = 2\n"
    "DEFAULT_STATE_PATH = Path(\"docs/pilot/live_pilot_state.json\")\n"
    "DEFAULT_MANIFEST_PATH = Path(\"docs/pilot/pilot_admission_manifest.json\")",
    1,
)
text = text.replace(
    "def new_state() -> dict[str, Any]:\n",
    "def verified_admission_binding(root: Path = ROOT) -> dict[str, Any]:\n"
    "    from pilot_admission import verify_default_admission\n"
    "    from pilot_evidence import load_json\n\n"
    "    errors = verify_default_admission(root)\n"
    "    if errors:\n"
    "        raise ValueError(\"Pilot admission is invalid: \" + \"; \".join(errors))\n"
    "    manifest_path = root / DEFAULT_MANIFEST_PATH\n"
    "    return build_admission_binding(manifest_path, load_json(manifest_path))\n\n\n"
    "def new_state(admission_binding: Mapping[str, Any]) -> dict[str, Any]:\n",
    1,
)
text = text.replace(
    '        "stop_reasons": [],\n        "scenarios": [_empty_record(scenario) for scenario in SCENARIOS],',
    '        "stop_reasons": [],\n'
    '        "admission": json.loads(json.dumps(dict(admission_binding))),\n'
    '        "scenarios": [_empty_record(scenario) for scenario in SCENARIOS],',
    1,
)
old_load = '''def load_state(path: Path) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Pilot state not found: {path}. Run init first.") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Pilot state is not valid JSON: {exc}") from exc
    if state.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Unsupported pilot state schema {state.get('schema_version')}; expected {SCHEMA_VERSION}")
    if [item.get("number") for item in _scenario_records(state)] != [item["number"] for item in SCENARIOS]:
        raise ValueError("Pilot state scenario order does not match the current 20-order contract")
    return state
'''
new_load = '''def load_state(
    path: Path,
    *,
    expected_admission: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Pilot state not found: {path}. Run init first.") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Pilot state is not valid JSON: {exc}") from exc
    schema = state.get("schema_version")
    if schema == 1:
        raise ValueError(
            "Legacy pilot state schema 1 cannot be reused. Archive it and initialize "
            "a fresh admission-bound pilot state."
        )
    if schema != SCHEMA_VERSION:
        raise ValueError(f"Unsupported pilot state schema {schema}; expected {SCHEMA_VERSION}")
    if [item.get("number") for item in _scenario_records(state)] != [item["number"] for item in SCENARIOS]:
        raise ValueError("Pilot state scenario order does not match the current 20-order contract")
    if expected_admission is not None:
        require_admission_binding(state, expected_admission)
    return state
'''
if text.count(old_load) != 1:
    raise SystemExit("pilot_control.py load_state block changed unexpectedly")
text = text.replace(old_load, new_load, 1)
text = text.replace("return _finish(path, new_state())", "return _finish(path, new_state(args.admission_binding))", 1)
text = text.replace("state = load_state(path)", "state = load_state(path, expected_admission=args.admission_binding)", 1)
text = text.replace("return _finish(path, load_state(path))", "return _finish(path, load_state(path, expected_admission=args.admission_binding))", 1)
text = text.replace("return _finish(path, load_state(path), final=args.final)", "return _finish(path, load_state(path, expected_admission=args.admission_binding), final=args.final)", 1)
text = text.replace(
    "    try:\n        return args.handler(args)\n    except ValueError as exc:\n",
    "    try:\n"
    "        args.admission_binding = verified_admission_binding(ROOT)\n"
    "        return args.handler(args)\n"
    "    except ValueError as exc:\n",
    1,
)
path.write_text(text, encoding="utf-8")

# Runner revalidates admission on every call, including an existing state.
Path("scripts/pilot_runner.py").write_text(
    '''#!/usr/bin/env python3
"""Admission-gated wrapper for every controlled 20-order pilot operation."""

from __future__ import annotations

import sys
from pathlib import Path

from pilot_admission import verify_default_admission
from pilot_control import main as pilot_control_main

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    errors = verify_default_admission(ROOT)
    if errors:
        print("Pilot runner blocked by admission policy:")
        for error in errors:
            print(f"- {error}")
        return 2
    return pilot_control_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
''',
    encoding="utf-8",
)

# All operator entry points pass through the gated runner.
makefile = Path("Makefile")
text = makefile.read_text(encoding="utf-8")
text = text.replace(
    "pilot-init:\n\tpython3 scripts/pilot_admission.py verify\n\tpython3 scripts/pilot_control.py init",
    "pilot-init:\n\tpython3 scripts/pilot_runner.py init",
    1,
)
text = text.replace("\tpython3 scripts/pilot_control.py record $(ARGS)", "\tpython3 scripts/pilot_runner.py record $(ARGS)", 1)
text = text.replace("\tpython3 scripts/pilot_control.py status", "\tpython3 scripts/pilot_runner.py status", 1)
text = text.replace("\tpython3 scripts/pilot_control.py validate --final", "\tpython3 scripts/pilot_runner.py validate --final", 1)
makefile.write_text(text, encoding="utf-8")

# Runtime arm validates the state against the same exact admission manifest.
path = Path("scripts/pilot_runtime.py")
text = path.read_text(encoding="utf-8")
text = text.replace(
    "    from pilot_admission import verify_default_admission\n",
    "    from pilot_admission import verify_default_admission\n"
    "    from pilot_control_binding import build_admission_binding, require_admission_binding\n",
    1,
)
text = text.replace(
    '        if pilot_state.get("schema_version") != 1:\n            raise ValueError("Pilot control state schema is unsupported")',
    '        if pilot_state.get("schema_version") != 2:\n'
    '            raise ValueError("Pilot control state schema is unsupported")\n'
    '        require_admission_binding(\n'
    '            pilot_state, build_admission_binding(DEFAULT_MANIFEST, manifest)\n'
    '        )',
    1,
)
path.write_text(text, encoding="utf-8")

# Backend validates the binding on every checkout, not only during arm.
path = Path("backend/services/pilot_runtime.py")
text = path.read_text(encoding="utf-8")
text = text.replace(
    "from scripts.pilot_release_contract import CAPABILITY_VERSION\n",
    "from scripts.pilot_release_contract import CAPABILITY_VERSION\n"
    "from scripts.pilot_control_binding import build_admission_binding, validate_admission_binding\n",
    1,
)
text = text.replace(
    '    if pilot_state.get("schema_version") != 1:\n        errors.append("pilot control state schema is unsupported")',
    '    if pilot_state.get("schema_version") != 2:\n'
    '        errors.append("pilot control state schema is unsupported")\n'
    '    else:\n'
    '        try:\n'
    '            expected_binding = build_admission_binding(manifest_path, manifest)\n'
    '            errors.extend(validate_admission_binding(pilot_state, expected_binding))\n'
    '        except (OSError, ValueError) as exc:\n'
    '            errors.append(str(exc))',
    1,
)
path.write_text(text, encoding="utf-8")

# Unit tests use an explicit immutable admission binding.
path = Path("backend/tests/test_pilot_control.py")
text = path.read_text(encoding="utf-8")
anchor = "from pilot_control import SCENARIOS, new_state, record_scenario, validate_state  # noqa: E402\n\n"
binding = '''from pilot_control import SCENARIOS, new_state, record_scenario, validate_state  # noqa: E402

ADMISSION_BINDING = {
    "manifest_sha256": "a" * 64,
    "created_at": "2026-08-05T12:00:00Z",
    "configuration_fingerprint": "b" * 64,
    "release": {
        "release_id": "pilot-release",
        "git_commit": "c" * 40,
        "sha256": "d" * 64,
    },
}

'''
if text.count(anchor) != 1:
    raise SystemExit("test_pilot_control.py import anchor changed unexpectedly")
text = text.replace(anchor, binding, 1).replace("new_state()", "new_state(ADMISSION_BINDING)")
path.write_text(text, encoding="utf-8")

Path("backend/tests/test_pilot_control_binding.py").write_text(
    '''import json
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
''',
    encoding="utf-8",
)

# Runtime test fixture creates schema v2 state bound to its signed admission.
path = Path("backend/tests/test_pilot_runtime.py")
text = path.read_text(encoding="utf-8")
text = text.replace(
    "from scripts.pilot_evidence import configuration_fingerprint, sign_payload\n",
    "from scripts.pilot_evidence import configuration_fingerprint, sign_payload\n"
    "from scripts.pilot_control_binding import build_admission_binding\n",
    1,
)
text = text.replace('            "version": 9,', '            "version": 10,', 1)
old_state = '''    pilot_created_at = "2026-08-03T18:00:00Z"
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
'''
new_state = '''    pilot_created_at = "2026-08-03T18:00:00Z"
    pilot_path = pilot_docs / "live_pilot_state.json"
    pilot_payload = {
        "schema_version": 2,
        "created_at": pilot_created_at,
        "decision": "NO-GO",
        "scenarios": [{} for _ in range(20)],
    }

    manifest_path = pilot_docs / "pilot_admission_manifest.json"
'''
if text.count(old_state) != 1:
    raise SystemExit("test_pilot_runtime.py pilot state fixture changed unexpectedly")
text = text.replace(old_state, new_state, 1)
text = text.replace(
    '    manifest = {\n        "kind": "pilot_admission",',
    '    manifest = {\n'
    '        "kind": "pilot_admission",\n'
    '        "created_at": "2026-08-05T12:00:00Z",',
    1,
)
old_manifest_write = '''    manifest_path.write_text(
        json.dumps(sign_payload(manifest, secret)),
        encoding="utf-8",
    )

    settings = SimpleNamespace(
'''
new_manifest_write = '''    manifest_path.write_text(
        json.dumps(sign_payload(manifest, secret)),
        encoding="utf-8",
    )
    signed_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pilot_payload["admission"] = build_admission_binding(manifest_path, signed_manifest)
    pilot_path.write_text(json.dumps(pilot_payload), encoding="utf-8")

    settings = SimpleNamespace(
'''
if text.count(old_manifest_write) != 1:
    raise SystemExit("test_pilot_runtime.py manifest write block changed unexpectedly")
text = text.replace(old_manifest_write, new_manifest_write, 1)
text += '''

def test_pilot_state_bound_to_other_admission_fails_closed(tmp_path):
    db, customer, settings, env, pilot_path, *_ = _runtime(tmp_path)
    payload = json.loads(pilot_path.read_text(encoding="utf-8"))
    payload["admission"]["manifest_sha256"] = "0" * 64
    pilot_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(HTTPException) as mismatch:
        acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    assert mismatch.value.status_code == 503
'''
path.write_text(text, encoding="utf-8")

# Immutable release capability v10 requires the complete admission-bound control plane.
path = Path("scripts/pilot_release_capability.py")
text = path.read_text(encoding="utf-8")
text = text.replace(
    '    "scripts/pilot_runtime.py",\n',
    '    "scripts/pilot_control_binding.py",\n'
    '    "scripts/pilot_control.py",\n'
    '    "scripts/pilot_runner.py",\n'
    '    "backend/tests/test_pilot_control_binding.py",\n'
    '    "Makefile",\n'
    '    "scripts/pilot_runtime.py",\n',
    1,
)
text = text.replace('(\"CAPABILITY_VERSION = 9\",)', '(\"CAPABILITY_VERSION = 10\",)', 1)
anchor = '            _require_markers(bundle, files, "backend/tests/test_pilot_admission.py", ("test_live_gate_rejects_tampering_configuration_and_other_release", "configuration fingerprint", "live gate release"), errors)\n'
guards = anchor + '''            _require_markers(bundle, files, "scripts/pilot_control_binding.py", ("def build_admission_binding(", "manifest_sha256", "def validate_admission_binding(", "def require_admission_binding("), errors)
            _require_markers(bundle, files, "scripts/pilot_control.py", ("SCHEMA_VERSION = 2", "def verified_admission_binding(", "expected_admission=args.admission_binding", "Legacy pilot state schema 1 cannot be reused"), errors)
            _require_markers(bundle, files, "scripts/pilot_runner.py", ("errors = verify_default_admission(ROOT)", "return pilot_control_main(args)"), errors)
            _require_markers(bundle, files, "backend/services/pilot_runtime.py", ("build_admission_binding(manifest_path, manifest)", "validate_admission_binding(pilot_state, expected_binding)"), errors)
            _require_markers(bundle, files, "scripts/pilot_runtime.py", ("build_admission_binding(DEFAULT_MANIFEST, manifest)", "require_admission_binding("), errors)
            _require_markers(bundle, files, "Makefile", ("python3 scripts/pilot_runner.py init", "python3 scripts/pilot_runner.py record $(ARGS)", "python3 scripts/pilot_runner.py status", "python3 scripts/pilot_runner.py validate --final"), errors)
            _require_markers(bundle, files, "backend/tests/test_pilot_control_binding.py", ("test_state_is_bound_to_one_exact_signed_admission_file", "test_legacy_state_is_rejected_without_silent_migration", "test_makefile_routes_pilot_control_through_admission_runner"), errors)
'''
if text.count(anchor) != 1:
    raise SystemExit("pilot_release_capability.py admission marker anchor changed unexpectedly")
path.write_text(text.replace(anchor, guards, 1), encoding="utf-8")

# Synthetic capability archive fixtures and repository test version.
path = Path("backend/tests/test_pilot_release_capability.py")
text = path.read_text(encoding="utf-8")
text = text.replace("CAPABILITY_VERSION = 9", "CAPABILITY_VERSION = 10")
text = text.replace("assert CAPABILITY_VERSION == 9", "assert CAPABILITY_VERSION == 10")
fixture_anchor = '    "backend/services/pilot_runtime.py": (\n        "from scripts.pilot_release_contract import CAPABILITY_VERSION\\n"\n        \'"version": CAPABILITY_VERSION\\n\'\n    ),\n'
fixture = '''    "backend/services/pilot_runtime.py": (
        "from scripts.pilot_release_contract import CAPABILITY_VERSION\\n"
        '"version": CAPABILITY_VERSION\\n'
        "build_admission_binding(manifest_path, manifest)\\n"
        "validate_admission_binding(pilot_state, expected_binding)\\n"
    ),
    "scripts/pilot_control_binding.py": (
        "def build_admission_binding(): pass\\nmanifest_sha256\\n"
        "def validate_admission_binding(): pass\\n"
        "def require_admission_binding(): pass\\n"
    ),
    "scripts/pilot_control.py": (
        "SCHEMA_VERSION = 2\\ndef verified_admission_binding(): pass\\n"
        "expected_admission=args.admission_binding\\n"
        "Legacy pilot state schema 1 cannot be reused\\n"
    ),
    "scripts/pilot_runner.py": (
        "errors = verify_default_admission(ROOT)\\nreturn pilot_control_main(args)\\n"
    ),
    "scripts/pilot_runtime.py": (
        "build_admission_binding(DEFAULT_MANIFEST, manifest)\\n"
        "require_admission_binding(\\n"
    ),
    "Makefile": (
        "python3 scripts/pilot_runner.py init\\n"
        "python3 scripts/pilot_runner.py record $(ARGS)\\n"
        "python3 scripts/pilot_runner.py status\\n"
        "python3 scripts/pilot_runner.py validate --final\\n"
    ),
    "backend/tests/test_pilot_control_binding.py": (
        "test_state_is_bound_to_one_exact_signed_admission_file\\n"
        "test_legacy_state_is_rejected_without_silent_migration\\n"
        "test_makefile_routes_pilot_control_through_admission_runner\\n"
    ),
'''
if text.count(fixture_anchor) != 1:
    raise SystemExit("capability synthetic runtime fixture anchor changed unexpectedly")
path.write_text(text.replace(fixture_anchor, fixture, 1), encoding="utf-8")

path = Path("backend/tests/test_pilot_release_capability_repository.py")
text = path.read_text(encoding="utf-8").replace("v9", "v10")
path.write_text(text, encoding="utf-8")

# E2E evidence matrix records the closed internal control gap.
path = Path("docs/pilot/end_to_end_coverage_matrix.md")
text = path.read_text(encoding="utf-8")
needle = "The live gate report is HMAC-signed and bound to the exact current release and configuration fingerprint before human admission can be created. Tampering, cross-release reuse and configuration drift are rejected."
replacement = needle + " The 20-scenario control state is then bound to that one exact signed admission manifest; every init, record, status, final validation, runtime arm and checkout revalidates the binding, and legacy unbound state is rejected without silent migration."
if text.count(needle) != 1:
    raise SystemExit("E2E matrix signed live gate evidence paragraph changed unexpectedly")
path.write_text(text.replace(needle, replacement, 1), encoding="utf-8")
