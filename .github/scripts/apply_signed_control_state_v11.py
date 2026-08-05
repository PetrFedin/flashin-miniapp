from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one occurrence, found {count}: {old[:120]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Shared release contract.
replace_once(
    "scripts/pilot_release_contract.py",
    "CAPABILITY_VERSION = 10",
    "CAPABILITY_VERSION = 11",
)

# Sign every pilot control state write and verify every read.
control = Path("scripts/pilot_control.py")
text = control.read_text(encoding="utf-8")
text = text.replace(
    "from pilot_control_binding import build_admission_binding, require_admission_binding\n"
    "from script_time import utc_timestamp\n",
    "from pilot_control_binding import build_admission_binding, require_admission_binding\n"
    "from pilot_evidence import require_signing_secret, sign_payload, verify_payload_signature\n"
    "from pilot_readiness import read_env\n"
    "from script_time import utc_timestamp\n",
    1,
)
text = text.replace("SCHEMA_VERSION = 2", "SCHEMA_VERSION = 3", 1)
anchor = (
    "    manifest_path = root / DEFAULT_MANIFEST_PATH\n"
    "    return build_admission_binding(manifest_path, load_json(manifest_path))\n"
)
insert = anchor + (
    "\n\ndef pilot_signing_secret(root: Path = ROOT) -> str:\n"
    "    return require_signing_secret(read_env(root / \".env\"))\n"
)
if text.count(anchor) != 1:
    raise SystemExit("pilot_control.py admission binding anchor changed unexpectedly")
text = text.replace(anchor, insert, 1)
start = text.index("def load_state(")
end = text.index("\n\ndef _atomic_write_text", start)
new_load = '''def load_state(
    path: Path,
    *,
    expected_admission: Mapping[str, Any] | None = None,
    secret: str | None = None,
) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Pilot state not found: {path}. Run init first.") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Pilot state is not valid JSON: {exc}") from exc
    if not isinstance(state, dict):
        raise ValueError("Pilot state must contain a JSON object")
    schema = state.get("schema_version")
    if schema == 1:
        raise ValueError(
            "Legacy pilot state schema 1 cannot be reused. Archive it and initialize "
            "a fresh signed admission-bound pilot state."
        )
    if schema == 2:
        raise ValueError(
            "Unsigned pilot state schema 2 cannot be reused. Archive it and initialize "
            "a fresh signed admission-bound pilot state."
        )
    if schema != SCHEMA_VERSION:
        raise ValueError(f"Unsupported pilot state schema {schema}; expected {SCHEMA_VERSION}")
    if not secret:
        raise ValueError("Pilot control signing secret is required")
    if not verify_payload_signature(state, secret):
        raise ValueError("Pilot control state signature is invalid")
    if [item.get("number") for item in _scenario_records(state)] != [item["number"] for item in SCENARIOS]:
        raise ValueError("Pilot state scenario order does not match the current 20-order contract")
    if expected_admission is not None:
        require_admission_binding(state, expected_admission)
    return state
'''
text = text[:start] + new_load + text[end:]
old_save = '''def save_state(path: Path, state: dict[str, Any], report: dict[str, Any]) -> None:
    state["updated_at"] = utc_timestamp()
    _apply_report(state, report)
    _atomic_write_text(path, json.dumps(state, ensure_ascii=False, indent=2) + "\\n")
    _atomic_write_text(path.with_name("live_pilot_summary.md"), render_markdown(state, report))
'''
new_save = '''def save_state(
    path: Path,
    state: dict[str, Any],
    report: dict[str, Any],
    *,
    secret: str,
) -> None:
    state["updated_at"] = utc_timestamp()
    _apply_report(state, report)
    signed_state = sign_payload(state, secret)
    state.clear()
    state.update(signed_state)
    _atomic_write_text(path, json.dumps(state, ensure_ascii=False, indent=2) + "\\n")
    _atomic_write_text(path.with_name("live_pilot_summary.md"), render_markdown(state, report))
'''
if text.count(old_save) != 1:
    raise SystemExit("pilot_control.py save_state block changed unexpectedly")
text = text.replace(old_save, new_save, 1)
text = text.replace(
    "def _finish(path: Path, state: dict[str, Any], *, final: bool = False) -> int:\n"
    "    report = validate_state(state, final=final)\n"
    "    save_state(path, state, report)\n",
    "def _finish(\n"
    "    path: Path,\n"
    "    state: dict[str, Any],\n"
    "    *,\n"
    "    secret: str,\n"
    "    final: bool = False,\n"
    ") -> int:\n"
    "    report = validate_state(state, final=final)\n"
    "    save_state(path, state, report, secret=secret)\n",
    1,
)
text = text.replace(
    "    return _finish(path, new_state(args.admission_binding))",
    "    return _finish(path, new_state(args.admission_binding), secret=args.signing_secret)",
    1,
)
text = text.replace(
    "    state = load_state(path, expected_admission=args.admission_binding)",
    "    state = load_state(\n"
    "        path, expected_admission=args.admission_binding, secret=args.signing_secret\n"
    "    )",
    1,
)
text = text.replace(
    "    return _finish(path, state)",
    "    return _finish(path, state, secret=args.signing_secret)",
    1,
)
text = text.replace(
    "    return _finish(path, load_state(path, expected_admission=args.admission_binding))",
    "    return _finish(\n"
    "        path,\n"
    "        load_state(\n"
    "            path, expected_admission=args.admission_binding, secret=args.signing_secret\n"
    "        ),\n"
    "        secret=args.signing_secret,\n"
    "    )",
    1,
)
text = text.replace(
    "    return _finish(path, load_state(path, expected_admission=args.admission_binding), final=args.final)",
    "    return _finish(\n"
    "        path,\n"
    "        load_state(\n"
    "            path, expected_admission=args.admission_binding, secret=args.signing_secret\n"
    "        ),\n"
    "        secret=args.signing_secret,\n"
    "        final=args.final,\n"
    "    )",
    1,
)
text = text.replace(
    "        args.admission_binding = verified_admission_binding(ROOT)\n"
    "        return args.handler(args)",
    "        args.admission_binding = verified_admission_binding(ROOT)\n"
    "        args.signing_secret = pilot_signing_secret(ROOT)\n"
    "        return args.handler(args)",
    1,
)
control.write_text(text, encoding="utf-8")

# Verify the signed state on every API checkout.
runtime_service = Path("backend/services/pilot_runtime.py")
text = runtime_service.read_text(encoding="utf-8")
old = '''    if pilot_state.get("schema_version") != 2:
        errors.append("pilot control state schema is unsupported")
    else:
        try:
            expected_binding = build_admission_binding(manifest_path, manifest)
            errors.extend(validate_admission_binding(pilot_state, expected_binding))
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
'''
new = '''    if pilot_state.get("schema_version") != 3:
        errors.append("pilot control state schema is unsupported")
    elif not verify_payload_signature(pilot_state, secret):
        errors.append("pilot control state signature is invalid")
    else:
        try:
            expected_binding = build_admission_binding(manifest_path, manifest)
            errors.extend(validate_admission_binding(pilot_state, expected_binding))
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
'''
if text.count(old) != 1:
    raise SystemExit("backend runtime pilot state block changed unexpectedly")
runtime_service.write_text(text.replace(old, new, 1), encoding="utf-8")

# Verify the signed state before arming runtime on the host.
runtime_cli = Path("scripts/pilot_runtime.py")
text = runtime_cli.read_text(encoding="utf-8")
text = text.replace(
    "    from pilot_admission import verify_default_admission\n"
    "    from pilot_control_binding import build_admission_binding, require_admission_binding\n",
    "    from pilot_admission import verify_default_admission\n"
    "    from pilot_control_binding import build_admission_binding, require_admission_binding\n"
    "    from pilot_evidence import require_signing_secret, verify_payload_signature\n",
    1,
)
text = text.replace(
    "        telegram_ids = _normalize_ids(args.telegram_id)\n"
    "        manifest = _load_json(DEFAULT_MANIFEST, \"pilot admission manifest\")",
    "        secret = require_signing_secret(env)\n"
    "        telegram_ids = _normalize_ids(args.telegram_id)\n"
    "        manifest = _load_json(DEFAULT_MANIFEST, \"pilot admission manifest\")",
    1,
)
text = text.replace(
    "        if pilot_state.get(\"schema_version\") != 2:\n"
    "            raise ValueError(\"Pilot control state schema is unsupported\")\n"
    "        require_admission_binding(\n",
    "        if pilot_state.get(\"schema_version\") != 3:\n"
    "            raise ValueError(\"Pilot control state schema is unsupported\")\n"
    "        if not verify_payload_signature(pilot_state, secret):\n"
    "            raise ValueError(\"Pilot control state signature is invalid\")\n"
    "        require_admission_binding(\n",
    1,
)
runtime_cli.write_text(text, encoding="utf-8")

# Dedicated signature contract tests.
Path("backend/tests/test_pilot_control_signature.py").write_text(
    '''import json
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
''',
    encoding="utf-8",
)

# Runtime fixtures issue v11 capabilities and signed state files.
runtime_tests = Path("backend/tests/test_pilot_runtime.py")
text = runtime_tests.read_text(encoding="utf-8")
text = text.replace('"version": 10,', '"version": 11,', 1)
text = text.replace('"schema_version": 2,', '"schema_version": 3,', 1)
text = text.replace(
    '    pilot_path.write_text(json.dumps(pilot_payload), encoding="utf-8")',
    '    pilot_path.write_text(\n'
    '        json.dumps(sign_payload(pilot_payload, secret)), encoding="utf-8"\n'
    '    )',
    1,
)
text = text.replace(
    '    payload["decision"] = "STOP"\n'
    '    pilot_path.write_text(json.dumps(payload), encoding="utf-8")',
    '    payload["decision"] = "STOP"\n'
    '    pilot_path.write_text(\n'
    '        json.dumps(sign_payload(payload, env["PILOT_EVIDENCE_SIGNING_SECRET"])),\n'
    '        encoding="utf-8",\n'
    '    )',
    1,
)
text = text.replace(
    '    payload["admission"]["manifest_sha256"] = "0" * 64\n'
    '    pilot_path.write_text(json.dumps(payload), encoding="utf-8")',
    '    payload["admission"]["manifest_sha256"] = "0" * 64\n'
    '    pilot_path.write_text(\n'
    '        json.dumps(sign_payload(payload, env["PILOT_EVIDENCE_SIGNING_SECRET"])),\n'
    '        encoding="utf-8",\n'
    '    )',
    1,
)
text += '''


def test_tampered_pilot_control_state_fails_closed_on_checkout(tmp_path):
    db, customer, settings, env, pilot_path, *_ = _runtime(tmp_path)
    payload = json.loads(pilot_path.read_text(encoding="utf-8"))
    payload["scenarios"][0]["result"] = "pass"
    pilot_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(HTTPException) as tampered:
        acquire_pilot_checkout(db, customer=customer, settings=settings, env=env)
    assert tampered.value.status_code == 503
'''
runtime_tests.write_text(text, encoding="utf-8")

# Host-arm wiring test.
runtime_cli_tests = Path("backend/tests/test_pilot_runtime_cli.py")
text = runtime_cli_tests.read_text(encoding="utf-8")
text += '''


def test_host_arm_requires_signed_schema_v3_control_state():
    source = (ROOT / "scripts/pilot_runtime.py").read_text(encoding="utf-8")
    assert 'pilot_state.get("schema_version") != 3' in source
    assert "verify_payload_signature(pilot_state, secret)" in source
    assert "Pilot control state signature is invalid" in source
'''
runtime_cli_tests.write_text(text, encoding="utf-8")

# Immutable release capability v11.
capability = Path("scripts/pilot_release_capability.py")
text = capability.read_text(encoding="utf-8")
text = text.replace(
    '    "backend/tests/test_pilot_control_binding.py",\n'
    '    "Makefile",\n',
    '    "backend/tests/test_pilot_control_binding.py",\n'
    '    "backend/tests/test_pilot_control_signature.py",\n'
    '    "backend/tests/test_pilot_runtime.py",\n'
    '    "Makefile",\n',
    1,
)
text = text.replace('(\"CAPABILITY_VERSION = 10\",)', '(\"CAPABILITY_VERSION = 11\",)', 1)
old_marker = '''            _require_markers(bundle, files, "scripts/pilot_control.py", ("SCHEMA_VERSION = 2", "def verified_admission_binding(", "expected_admission=args.admission_binding", "Legacy pilot state schema 1 cannot be reused"), errors)
'''
new_marker = '''            _require_markers(bundle, files, "scripts/pilot_control.py", ("SCHEMA_VERSION = 3", "def verified_admission_binding(", "def pilot_signing_secret(", "verify_payload_signature(state, secret)", "signed_state = sign_payload(state, secret)", "Unsigned pilot state schema 2 cannot be reused", "secret=args.signing_secret"), errors)
'''
if text.count(old_marker) != 1:
    raise SystemExit("capability pilot control marker changed unexpectedly")
text = text.replace(old_marker, new_marker, 1)
text = text.replace(
    '            _require_markers(bundle, files, "backend/services/pilot_runtime.py", ("build_admission_binding(manifest_path, manifest)", "validate_admission_binding(pilot_state, expected_binding)"), errors)\n',
    '            _require_markers(bundle, files, "backend/services/pilot_runtime.py", ("build_admission_binding(manifest_path, manifest)", "validate_admission_binding(pilot_state, expected_binding)", "verify_payload_signature(pilot_state, secret)", "pilot control state signature is invalid"), errors)\n',
    1,
)
text = text.replace(
    '            _require_markers(bundle, files, "scripts/pilot_runtime.py", ("build_admission_binding(DEFAULT_MANIFEST, manifest)", "require_admission_binding("), errors)\n',
    '            _require_markers(bundle, files, "scripts/pilot_runtime.py", ("build_admission_binding(DEFAULT_MANIFEST, manifest)", "require_admission_binding(", "verify_payload_signature(pilot_state, secret)", "Pilot control state signature is invalid"), errors)\n',
    1,
)
anchor = '            _require_markers(bundle, files, "backend/tests/test_pilot_control_binding.py", ("test_state_is_bound_to_one_exact_signed_admission_file", "test_legacy_state_is_rejected_without_silent_migration", "test_makefile_routes_pilot_control_through_admission_runner"), errors)\n'
addition = anchor + (
    '            _require_markers(bundle, files, "backend/tests/test_pilot_control_signature.py", '
    '("test_state_write_is_signed_and_exact_state_loads", "test_tampered_scenario_or_decision_is_rejected", '
    '"test_wrong_secret_and_unsigned_schema_v2_are_rejected"), errors)\n'
    '            _require_markers(bundle, files, "backend/tests/test_pilot_runtime.py", '
    '("test_tampered_pilot_control_state_fails_closed_on_checkout", "sign_payload(pilot_payload, secret)"), errors)\n'
)
if text.count(anchor) != 1:
    raise SystemExit("capability pilot binding test marker changed unexpectedly")
capability.write_text(text.replace(anchor, addition, 1), encoding="utf-8")

capability_tests = Path("backend/tests/test_pilot_release_capability.py")
text = capability_tests.read_text(encoding="utf-8")
text = text.replace("CAPABILITY_VERSION = 10", "CAPABILITY_VERSION = 11")
text = text.replace("assert CAPABILITY_VERSION == 10", "assert CAPABILITY_VERSION == 11")
text = text.replace(
    '        "validate_admission_binding(pilot_state, expected_binding)\\n"\n',
    '        "validate_admission_binding(pilot_state, expected_binding)\\n"\n'
    '        "verify_payload_signature(pilot_state, secret)\\n"\n'
    '        "pilot control state signature is invalid\\n"\n',
    1,
)
old_fixture = '''    "scripts/pilot_control.py": (
        "SCHEMA_VERSION = 2\\ndef verified_admission_binding(): pass\\n"
        "expected_admission=args.admission_binding\\n"
        "Legacy pilot state schema 1 cannot be reused\\n"
    ),
'''
new_fixture = '''    "scripts/pilot_control.py": (
        "SCHEMA_VERSION = 3\\ndef verified_admission_binding(): pass\\n"
        "def pilot_signing_secret(): pass\\n"
        "verify_payload_signature(state, secret)\\n"
        "signed_state = sign_payload(state, secret)\\n"
        "Unsigned pilot state schema 2 cannot be reused\\n"
        "secret=args.signing_secret\\n"
    ),
'''
if text.count(old_fixture) != 1:
    raise SystemExit("capability synthetic pilot control fixture changed unexpectedly")
text = text.replace(old_fixture, new_fixture, 1)
text = text.replace(
    '    "scripts/pilot_runtime.py": (\n'
    '        "build_admission_binding(DEFAULT_MANIFEST, manifest)\\n"\n'
    '        "require_admission_binding(\\n"\n'
    '    ),\n',
    '    "scripts/pilot_runtime.py": (\n'
    '        "build_admission_binding(DEFAULT_MANIFEST, manifest)\\n"\n'
    '        "require_admission_binding(\\n"\n'
    '        "verify_payload_signature(pilot_state, secret)\\n"\n'
    '        "Pilot control state signature is invalid\\n"\n'
    '    ),\n',
    1,
)
fixture_anchor = '''    "backend/tests/test_pilot_control_binding.py": (
        "test_state_is_bound_to_one_exact_signed_admission_file\\n"
        "test_legacy_state_is_rejected_without_silent_migration\\n"
        "test_makefile_routes_pilot_control_through_admission_runner\\n"
    ),
'''
fixture_addition = fixture_anchor + '''    "backend/tests/test_pilot_control_signature.py": (
        "test_state_write_is_signed_and_exact_state_loads\\n"
        "test_tampered_scenario_or_decision_is_rejected\\n"
        "test_wrong_secret_and_unsigned_schema_v2_are_rejected\\n"
    ),
    "backend/tests/test_pilot_runtime.py": (
        "test_tampered_pilot_control_state_fails_closed_on_checkout\\n"
        "sign_payload(pilot_payload, secret)\\n"
    ),
'''
if text.count(fixture_anchor) != 1:
    raise SystemExit("capability synthetic test fixture anchor changed unexpectedly")
capability_tests.write_text(text.replace(fixture_anchor, fixture_addition, 1), encoding="utf-8")

repository_test = Path("backend/tests/test_pilot_release_capability_repository.py")
text = repository_test.read_text(encoding="utf-8").replace("v10", "v11")
repository_test.write_text(text, encoding="utf-8")

# Operator migration/runbook and coverage matrix.
Path("docs/pilot/admission_bound_state_migration.md").write_text(
    '''# Signed admission-bound pilot state migration

This runbook applies when upgrading the controlled first-20-order pilot to state schema v3.

## Safety invariant

One `live_pilot_state.json` belongs to one exact signed `pilot_admission_manifest.json` and is itself HMAC-SHA256 signed. The admission binding includes the manifest SHA-256, creation time, configuration fingerprint, release ID, Git commit and release archive SHA-256. The state signature covers every scenario result, evidence reference, money/stock field, summary and GO/NO-GO/STOP decision.

Do not reuse a pilot state after any admission, signing-secret, configuration or promoted-release change. Do not edit the JSON by hand.

## Before initialization

1. Confirm that the production `.env` contains the intended provider and pilot settings plus the protected `PILOT_EVIDENCE_SIGNING_SECRET`.
2. Confirm that current and previous release pointers are different and both expose signed pilot capability v11.
3. Generate fresh provider, live-gate and rollback evidence.
4. Create the signed admission manifest with named owners and all required acknowledgements.
5. Run `make pilot-admission-status`; continue only when it returns GO with no errors.

## Existing schema v1 or v2 state

Schema v1 is admission-unbound. Schema v2 is admission-bound but unsigned. Both are intentionally rejected and are never migrated in place.

1. Stop the pilot runtime.
2. Copy `docs/pilot/live_pilot_state.json` and `docs/pilot/live_pilot_summary.md` to an access-controlled evidence archive with a timestamp.
3. Record the archive location and SHA-256 in the change log or incident record.
4. Remove the active legacy state only after the archive has been verified.
5. Create a fresh signed schema v3 state with `make pilot-init`.
6. Run `make pilot-status` and confirm there is no signature or admission-binding error.

Never edit the schema number manually and never copy scenario results into a state bound to another admission.

## Normal operator commands

```bash
make pilot-init
make pilot-record ARGS='--number 1 --result running --evidence <reference>'
make pilot-status
make pilot-final
```

Every target revalidates the signed admission, verifies the current state signature before reading it, and writes a new signature after an authorized state change. Direct calls that bypass `scripts/pilot_runner.py` are not part of the supported procedure.

## Expected fail-closed conditions

Stop and investigate when any command reports:

- pilot control state signature invalid;
- admission manifest checksum mismatch;
- configuration fingerprint mismatch;
- release ID, Git commit or archive SHA mismatch;
- expired provider, live-gate or admission evidence;
- legacy schema v1 or unsigned schema v2 state;
- missing or malformed admission binding;
- pilot decision `STOP`.

Do not use `--force` to suppress a mismatch. It is only for an intentional reset after the old evidence has been archived and the reset has an accountable owner.

## Runtime arm and checkout

After initialization, arm the allowlist with `make pilot-runtime-arm ARGS='--telegram-id <id>'`. Runtime arm and every checkout independently verify the state HMAC and compare the state with the exact signed admission. A signature or binding mismatch keeps checkout closed.

## Release, configuration or signing-secret change during the pilot

1. Stop runtime immediately.
2. Archive the current state and summary.
3. Generate fresh evidence for the new release/configuration.
4. Create a new signed admission.
5. Initialize a fresh signed schema v3 state.
6. Re-arm runtime only after admission and state verification pass.

Scenario results from the previous admission remain evidence for that run; they do not count toward the new run.
''',
    encoding="utf-8",
)

matrix = Path("docs/pilot/end_to_end_coverage_matrix.md")
text = matrix.read_text(encoding="utf-8")
text = text.replace(
    "| Runtime pilot guard | Allowlist -> signed release/config-bound live gate -> human admission -> first 20 orders -> automatic STOP | Capability v9, admission/runtime tests and circuit-breaker tests | PASS | Requires fresh signed provider/live evidence and controlled live run |",
    "| Runtime pilot guard | Allowlist -> signed live gate -> signed human admission -> signed 20-scenario state -> first 20 orders -> automatic STOP | Capability v11, state-signature/admission/runtime tests and circuit-breaker tests | PASS | Requires fresh signed provider/live evidence and controlled live run |",
)
text = text.replace(
    "The live gate report is HMAC-signed and bound to the exact current release and configuration fingerprint before human admission can be created. The admission create path validates that signed binding before it writes the manifest. Tampering, cross-release reuse and configuration drift are rejected. The 20-scenario control state is then bound to that one exact signed admission manifest; every init, record, status, final validation, runtime arm and checkout revalidates the binding, and legacy unbound state is rejected without silent migration.",
    "The live gate report is HMAC-signed and bound to the exact current release and configuration fingerprint before human admission can be created. The admission create path validates that signed binding before it writes the manifest. Tampering, cross-release reuse and configuration drift are rejected. The 20-scenario control state is bound to that exact admission and is independently HMAC-signed after every authorized write. CLI reads, runtime arm and every checkout verify the state signature before using scenario evidence or the GO/NO-GO/STOP decision. Legacy unbound schema v1, unsigned schema v2, manual edits and wrong-secret signatures fail closed.",
)
matrix.write_text(text, encoding="utf-8")
