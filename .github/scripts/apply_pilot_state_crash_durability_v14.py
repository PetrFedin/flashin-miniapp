from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one occurrence, found {count}: {old[:160]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "scripts/pilot_release_contract.py",
    "CAPABILITY_VERSION = 13",
    "CAPABILITY_VERSION = 14",
)

Path("scripts/pilot_control_io.py").write_text(
    '''"""Crash-durable atomic filesystem writes for pilot control evidence."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def durable_atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    replaced = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        replaced = True
        _fsync_directory(path.parent)
    except OSError as exc:
        raise ValueError(f"Durable pilot evidence write failed for {path}: {exc}") from exc
    finally:
        if not replaced and temporary_path.exists():
            temporary_path.unlink()
''',
    encoding="utf-8",
)

control = Path("scripts/pilot_control.py")
text = control.read_text(encoding="utf-8")
text = text.replace("import os\nimport tempfile\n", "", 1)
text = text.replace(
    "from pilot_control_lock import (\n"
    "    DEFAULT_LOCK_TIMEOUT_SECONDS,\n"
    "    exclusive_state_lock,\n"
    ")\n",
    "from pilot_control_io import durable_atomic_write_text\n"
    "from pilot_control_lock import (\n"
    "    DEFAULT_LOCK_TIMEOUT_SECONDS,\n"
    "    exclusive_state_lock,\n"
    ")\n",
    1,
)
start = text.index("def _atomic_write_text(")
end = text.index("\n\ndef _apply_report", start)
text = text[:start] + text[end + 2 :]
text = text.replace(
    '        _atomic_write_text(path, json.dumps(state, ensure_ascii=False, indent=2) + "\\n")\n'
    '        _atomic_write_text(path.with_name("live_pilot_summary.md"), render_markdown(state, report))\n',
    '        durable_atomic_write_text(\n'
    '            path, json.dumps(state, ensure_ascii=False, indent=2) + "\\n"\n'
    '        )\n'
    '        durable_atomic_write_text(\n'
    '            path.with_name("live_pilot_summary.md"),\n'
    '            render_markdown(state, report),\n'
    '        )\n',
    1,
)
old_render = '''def render_markdown(state: dict[str, Any], report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# FLASHIN live pilot control", "", f"**Decision:** {report['decision']}", "",
        f"Passed: {summary['passed']}/20 · Failed: {summary['failed']} · Blocked: {summary['blocked']} · Running: {summary['running']} · Todo: {summary['todo']}", "",
    ]
'''
new_render = '''def render_markdown(state: dict[str, Any], report: dict[str, Any]) -> str:
    summary = report["summary"]
    state_sha = signed_state_sha256(state)
    lines = [
        "# FLASHIN live pilot control",
        "",
        f"**Decision:** {report['decision']}",
        "",
        f"State revision: `{state.get('revision')}`",
        f"State SHA-256: `{state_sha}`",
        "Source: signed JSON state. This Markdown file is derived and non-authoritative.",
        "",
        f"Passed: {summary['passed']}/20 · Failed: {summary['failed']} · Blocked: {summary['blocked']} · Running: {summary['running']} · Todo: {summary['todo']}",
        "",
    ]
'''
if text.count(old_render) != 1:
    raise SystemExit("pilot_control.py render header changed unexpectedly")
text = text.replace(old_render, new_render, 1)
finish_anchor = "\n\ndef _state_path(args: argparse.Namespace) -> Path:\n"
refresh = '''

def refresh_summary(
    path: Path,
    *,
    expected_admission: Mapping[str, Any],
    secret: str,
    final: bool = False,
    lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> tuple[dict[str, Any], dict[str, Any]]:
    with exclusive_state_lock(path, timeout_seconds=lock_timeout_seconds):
        state = load_state(
            path,
            expected_admission=expected_admission,
            secret=secret,
        )
        report = validate_state(state, final=final)
        durable_atomic_write_text(
            path.with_name("live_pilot_summary.md"),
            render_markdown(state, report),
        )
        return state, report
'''
if text.count(finish_anchor) != 1:
    raise SystemExit("pilot_control.py state path anchor changed unexpectedly")
text = text.replace(finish_anchor, refresh + finish_anchor, 1)
old_finish_tail = '''    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["decision"] == "STOP":
        return 2
    if final and report["decision"] != "GO":
        return 1
    return 0
'''
new_finish_tail = '''    return _report_exit(report, final=final)


def _report_exit(report: Mapping[str, Any], *, final: bool) -> int:
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["decision"] == "STOP":
        return 2
    if final and report["decision"] != "GO":
        return 1
    return 0
'''
if text.count(old_finish_tail) != 1:
    raise SystemExit("pilot_control.py finish exit block changed unexpectedly")
text = text.replace(old_finish_tail, new_finish_tail, 1)
old_status = '''def command_status(args: argparse.Namespace) -> int:
    path = _state_path(args)
    return _finish(
        path,
        load_state(
            path, expected_admission=args.admission_binding, secret=args.signing_secret
        ),
        secret=args.signing_secret,
        persist=False,
    )


def command_validate(args: argparse.Namespace) -> int:
    path = _state_path(args)
    return _finish(
        path,
        load_state(
            path, expected_admission=args.admission_binding, secret=args.signing_secret
        ),
        secret=args.signing_secret,
        final=args.final,
        persist=False,
    )
'''
new_status = '''def command_status(args: argparse.Namespace) -> int:
    path = _state_path(args)
    _, report = refresh_summary(
        path,
        expected_admission=args.admission_binding,
        secret=args.signing_secret,
    )
    return _report_exit(report, final=False)


def command_validate(args: argparse.Namespace) -> int:
    path = _state_path(args)
    _, report = refresh_summary(
        path,
        expected_admission=args.admission_binding,
        secret=args.signing_secret,
        final=args.final,
    )
    return _report_exit(report, final=args.final)
'''
if text.count(old_status) != 1:
    raise SystemExit("pilot_control.py read-only command block changed unexpectedly")
control.write_text(text.replace(old_status, new_status, 1), encoding="utf-8")

Path("backend/tests/test_pilot_control_durability.py").write_text(
    '''import json
import os
from pathlib import Path
import stat
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import pilot_control as pilot_control_module  # noqa: E402
import pilot_control_io  # noqa: E402
from pilot_control import (  # noqa: E402
    load_state,
    new_state,
    refresh_summary,
    save_state,
    validate_state,
)
from pilot_control_chain import state_anchor  # noqa: E402
from pilot_control_io import durable_atomic_write_text  # noqa: E402

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


def test_durable_atomic_write_fsyncs_file_and_parent_directory(tmp_path: Path, monkeypatch):
    kinds: list[str] = []
    real_fsync = pilot_control_io.os.fsync

    def tracked_fsync(descriptor: int) -> None:
        mode = os.fstat(descriptor).st_mode
        kinds.append("directory" if stat.S_ISDIR(mode) else "file")
        real_fsync(descriptor)

    monkeypatch.setattr(pilot_control_io.os, "fsync", tracked_fsync)
    target = tmp_path / "evidence.txt"
    durable_atomic_write_text(target, "durable\\n")

    assert target.read_text(encoding="utf-8") == "durable\\n"
    assert kinds.count("file") >= 1
    assert kinds.count("directory") >= 1
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_summary_refresh_repairs_stale_file_without_advancing_state(tmp_path: Path):
    path = tmp_path / "live_pilot_state.json"
    state = _signed_state(path)
    before = path.read_bytes()
    anchor = state_anchor(state)
    summary_path = path.with_name("live_pilot_summary.md")
    summary_path.write_text("stale summary", encoding="utf-8")

    refreshed, report = refresh_summary(
        path,
        expected_admission=BINDING,
        secret=SECRET,
    )

    assert path.read_bytes() == before
    assert refreshed["revision"] == anchor["revision"]
    assert report["decision"] == "NO-GO"
    summary = summary_path.read_text(encoding="utf-8")
    assert f"State revision: `{anchor['revision']}`" in summary
    assert f"State SHA-256: `{anchor['sha256']}`" in summary
    assert "derived and non-authoritative" in summary


def test_summary_write_failure_leaves_valid_committed_state_and_is_repairable(
    tmp_path: Path,
    monkeypatch,
):
    path = tmp_path / "live_pilot_state.json"
    _signed_state(path)
    state = load_state(path, expected_admission=BINDING, secret=SECRET)
    state["scenarios"][0]["result"] = "running"
    report = validate_state(state, final=False)
    real_write = pilot_control_module.durable_atomic_write_text

    def fail_summary(target: Path, content: str) -> None:
        if target.name == "live_pilot_summary.md":
            raise ValueError("simulated summary crash")
        real_write(target, content)

    monkeypatch.setattr(
        pilot_control_module,
        "durable_atomic_write_text",
        fail_summary,
    )
    with pytest.raises(ValueError, match="simulated summary crash"):
        save_state(path, state, report, secret=SECRET)

    committed = load_state(path, expected_admission=BINDING, secret=SECRET)
    assert committed["revision"] == 2
    assert committed["scenarios"][0]["result"] == "running"

    monkeypatch.setattr(
        pilot_control_module,
        "durable_atomic_write_text",
        real_write,
    )
    refreshed, _ = refresh_summary(
        path,
        expected_admission=BINDING,
        secret=SECRET,
    )
    anchor = state_anchor(refreshed)
    summary = path.with_name("live_pilot_summary.md").read_text(encoding="utf-8")
    assert f"State revision: `{anchor['revision']}`" in summary
    assert f"State SHA-256: `{anchor['sha256']}`" in summary


def test_status_summary_refresh_does_not_change_signed_json_bytes(tmp_path: Path):
    path = tmp_path / "live_pilot_state.json"
    _signed_state(path)
    before = path.read_bytes()
    refresh_summary(path, expected_admission=BINDING, secret=SECRET, final=True)
    assert path.read_bytes() == before
''',
    encoding="utf-8",
)

# Runtime capability fixture must match v14 immediately.
runtime_tests = Path("backend/tests/test_pilot_runtime.py")
text = runtime_tests.read_text(encoding="utf-8")
if text.count('            "version": 13,\n') != 1:
    raise SystemExit("runtime capability v13 fixture changed unexpectedly")
runtime_tests.write_text(
    text.replace('            "version": 13,\n', '            "version": 14,\n', 1),
    encoding="utf-8",
)

capability = Path("scripts/pilot_release_capability.py")
text = capability.read_text(encoding="utf-8")
text = text.replace(
    '    "scripts/pilot_control_lock.py",\n'
    '    "scripts/pilot_control.py",\n',
    '    "scripts/pilot_control_lock.py",\n'
    '    "scripts/pilot_control_io.py",\n'
    '    "scripts/pilot_control.py",\n',
    1,
)
text = text.replace(
    '    "backend/tests/test_pilot_control_signature.py",\n'
    '    "backend/tests/test_pilot_runtime.py",\n',
    '    "backend/tests/test_pilot_control_signature.py",\n'
    '    "backend/tests/test_pilot_control_durability.py",\n'
    '    "backend/tests/test_pilot_runtime.py",\n',
    1,
)
text = text.replace('(\"CAPABILITY_VERSION = 13\",)', '(\"CAPABILITY_VERSION = 14\",)', 1)
text = text.replace(
    '_require_markers(bundle, files, "scripts/pilot_control.py", ("SCHEMA_VERSION = 4", "exclusive_state_lock(path, timeout_seconds=lock_timeout_seconds)", "Pilot control state appeared concurrently before initialization", "previous_hash = signed_state_sha256(parent_state)", "allow_replace=args.force", "persist=False"), errors)',
    '_require_markers(bundle, files, "scripts/pilot_control.py", ("SCHEMA_VERSION = 4", "durable_atomic_write_text(", "def refresh_summary(", "derived and non-authoritative", "return _report_exit(report, final=args.final)"), errors)',
    1,
)
lock_anchor = '            _require_markers(bundle, files, "scripts/pilot_control_lock.py", ("def exclusive_state_lock(", "fcntl.LOCK_EX | fcntl.LOCK_NB", "Pilot control state lock acquisition timed out", "os.fchmod(handle.fileno(), 0o600)"), errors)\n'
lock_addition = lock_anchor + (
    '            _require_markers(bundle, files, "scripts/pilot_control_io.py", '
    '("def durable_atomic_write_text(", "os.fsync(handle.fileno())", '
    '"os.replace(temporary_path, path)", "_fsync_directory(path.parent)", '
    '"os.fchmod(handle.fileno(), 0o600)"), errors)\n'
)
if text.count(lock_anchor) != 1:
    raise SystemExit("capability lock marker anchor changed unexpectedly")
text = text.replace(lock_anchor, lock_addition, 1)
test_anchor = '            _require_markers(bundle, files, "backend/tests/test_pilot_control_signature.py", ("test_cross_process_writers_serialize_and_reject_stale_parent", "test_cross_process_lock_timeout_fails_closed", "multiprocessing.get_context(\\"fork\\")"), errors)\n'
test_addition = test_anchor + (
    '            _require_markers(bundle, files, "backend/tests/test_pilot_control_durability.py", '
    '("test_durable_atomic_write_fsyncs_file_and_parent_directory", '
    '"test_summary_refresh_repairs_stale_file_without_advancing_state", '
    '"test_summary_write_failure_leaves_valid_committed_state_and_is_repairable", '
    '"test_status_summary_refresh_does_not_change_signed_json_bytes"), errors)\n'
)
if text.count(test_anchor) != 1:
    raise SystemExit("capability signature test marker anchor changed unexpectedly")
capability.write_text(text.replace(test_anchor, test_addition, 1), encoding="utf-8")

capability_tests = Path("backend/tests/test_pilot_release_capability.py")
text = capability_tests.read_text(encoding="utf-8")
text = text.replace("CAPABILITY_VERSION = 13", "CAPABILITY_VERSION = 14")
text = text.replace("assert CAPABILITY_VERSION == 13", "assert CAPABILITY_VERSION == 14")
old_control_fixture = '''    "scripts/pilot_control.py": (
        "SCHEMA_VERSION = 4\\n"
        "exclusive_state_lock(path, timeout_seconds=lock_timeout_seconds)\\n"
        "Pilot control state appeared concurrently before initialization\\n"
        "previous_hash = signed_state_sha256(parent_state)\\n"
        "allow_replace=args.force\\npersist=False\\n"
    ),
'''
new_control_fixture = '''    "scripts/pilot_control.py": (
        "SCHEMA_VERSION = 4\\n"
        "durable_atomic_write_text(\\n"
        "def refresh_summary(): pass\\n"
        "derived and non-authoritative\\n"
        "return _report_exit(report, final=args.final)\\n"
    ),
'''
if text.count(old_control_fixture) != 1:
    raise SystemExit("capability pilot control fixture changed unexpectedly")
text = text.replace(old_control_fixture, new_control_fixture, 1)
lock_fixture = '''    "scripts/pilot_control_lock.py": (
        "def exclusive_state_lock(): pass\\n"
        "fcntl.LOCK_EX | fcntl.LOCK_NB\\n"
        "Pilot control state lock acquisition timed out\\n"
        "os.fchmod(handle.fileno(), 0o600)\\n"
    ),
'''
io_fixture = lock_fixture + '''    "scripts/pilot_control_io.py": (
        "def durable_atomic_write_text(): pass\\n"
        "os.fsync(handle.fileno())\\n"
        "os.replace(temporary_path, path)\\n"
        "_fsync_directory(path.parent)\\n"
        "os.fchmod(handle.fileno(), 0o600)\\n"
    ),
'''
if text.count(lock_fixture) != 1:
    raise SystemExit("capability lock fixture changed unexpectedly")
text = text.replace(lock_fixture, io_fixture, 1)
signature_fixture = '''    "backend/tests/test_pilot_control_signature.py": (
        "test_cross_process_writers_serialize_and_reject_stale_parent\\n"
        "test_cross_process_lock_timeout_fails_closed\\n"
        'multiprocessing.get_context("fork")\\n'
    ),
'''
durability_fixture = signature_fixture + '''    "backend/tests/test_pilot_control_durability.py": (
        "test_durable_atomic_write_fsyncs_file_and_parent_directory\\n"
        "test_summary_refresh_repairs_stale_file_without_advancing_state\\n"
        "test_summary_write_failure_leaves_valid_committed_state_and_is_repairable\\n"
        "test_status_summary_refresh_does_not_change_signed_json_bytes\\n"
    ),
'''
if text.count(signature_fixture) != 1:
    raise SystemExit("capability signature fixture changed unexpectedly")
capability_tests.write_text(
    text.replace(signature_fixture, durability_fixture, 1),
    encoding="utf-8",
)

repository_test = Path("backend/tests/test_pilot_release_capability_repository.py")
text = repository_test.read_text(encoding="utf-8").replace("v13", "v14")
repository_test.write_text(text, encoding="utf-8")

runbook = Path("docs/pilot/admission_bound_state_migration.md")
text = runbook.read_text(encoding="utf-8")
text = text.replace("capability v13", "capability v14")
text = text.replace(
    "Authorized record changes hold a cross-process exclusive lock while they reread and verify the exact parent file, append its SHA-256, increment the revision, and atomically write both state and summary.",
    "Authorized record changes hold a cross-process exclusive lock while they reread and verify the exact parent file, append its SHA-256 and increment the revision. The signed JSON state is replaced first with file and parent-directory fsync; the Markdown summary is then regenerated as a derived, non-authoritative view with the exact state revision and SHA-256.",
)
text = text.replace(
    "Lock acquisition has a bounded timeout and fails closed.",
    "Lock acquisition has a bounded timeout and fails closed. If a process or host stops after the authoritative state commit but before the derived summary commit, the next `pilot-status` or `pilot-final` validates the signed state and repairs the summary without advancing the revision.",
)
text = text.replace(
    "- pilot state lock acquisition timeout;\n",
    "- pilot state lock acquisition timeout;\n- durable file or parent-directory fsync failure;\n",
)
runbook.write_text(text, encoding="utf-8")

matrix = Path("docs/pilot/end_to_end_coverage_matrix.md")
text = matrix.read_text(encoding="utf-8")
text = text.replace(
    "| Runtime pilot guard | Allowlist -> signed live gate -> signed admission -> locked replay-resistant 20-scenario lineage -> first 20 orders -> automatic STOP | Capability v13, cross-process lock/lineage/DB-anchor/admission/runtime tests and circuit-breaker tests | PASS | Requires fresh signed provider/live evidence and controlled live run |",
    "| Runtime pilot guard | Allowlist -> signed live gate -> signed admission -> crash-durable locked replay-resistant lineage -> first 20 orders -> automatic STOP | Capability v14, fsync/summary-repair/lock/lineage/DB-anchor/admission/runtime tests | PASS | Requires fresh signed provider/live evidence and controlled live run |",
)
text = text.replace(
    "State mutations are serialized by an OS advisory lock; true cross-process tests prove that only one competing writer succeeds and the stale writer fails closed.",
    "State mutations are serialized by an OS advisory lock; true cross-process tests prove that only one competing writer succeeds and the stale writer fails closed. State replacement fsyncs both the file and parent directory. The JSON is authoritative; a stale or missing derived Markdown summary is repaired from the signed state without creating a new revision.",
)
matrix.write_text(text, encoding="utf-8")
