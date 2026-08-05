from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one occurrence, found {count}: {old[:140]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "scripts/pilot_release_contract.py",
    "CAPABILITY_VERSION = 12",
    "CAPABILITY_VERSION = 13",
)

Path("scripts/pilot_control_lock.py").write_text(
    '''"""Cross-process fail-closed lock for pilot control state mutations."""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import time
from typing import Iterator

DEFAULT_LOCK_TIMEOUT_SECONDS = 10.0
_LOCK_POLL_SECONDS = 0.05


def lock_path_for(state_path: Path) -> Path:
    return state_path.with_name(f"{state_path.name}.lock")


@contextmanager
def exclusive_state_lock(
    state_path: Path,
    *,
    timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> Iterator[Path]:
    try:
        timeout = float(timeout_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("Pilot control lock timeout must be a number") from exc
    if timeout <= 0 or timeout > 60:
        raise ValueError("Pilot control lock timeout must be between 0 and 60 seconds")

    lock_path = lock_path_for(state_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    handle = os.fdopen(descriptor, "a+", encoding="utf-8")
    acquired = False
    deadline = time.monotonic() + timeout
    try:
        os.fchmod(handle.fileno(), 0o600)
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError(
                        f"Pilot control state lock acquisition timed out: {lock_path}"
                    )
                time.sleep(min(_LOCK_POLL_SECONDS, remaining))
        yield lock_path
    finally:
        if acquired:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
''',
    encoding="utf-8",
)

control = Path("scripts/pilot_control.py")
text = control.read_text(encoding="utf-8")
text = text.replace(
    "from pilot_control_chain import (\n"
    "    require_state_chain,\n"
    "    signed_state_sha256,\n"
    ")\n",
    "from pilot_control_chain import (\n"
    "    require_state_chain,\n"
    "    signed_state_sha256,\n"
    ")\n"
    "from pilot_control_lock import (\n"
    "    DEFAULT_LOCK_TIMEOUT_SECONDS,\n"
    "    exclusive_state_lock,\n"
    ")\n",
    1,
)
start = text.index("def save_state(")
end = text.index("\n\ndef _decimal", start)
new_save = '''def save_state(
    path: Path,
    state: dict[str, Any],
    report: dict[str, Any],
    *,
    secret: str,
    allow_replace: bool = False,
    lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
) -> None:
    with exclusive_state_lock(path, timeout_seconds=lock_timeout_seconds):
        if "signature" in state:
            try:
                parent_state = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError as exc:
                raise ValueError("Pilot control parent state is missing before update") from exc
            except json.JSONDecodeError as exc:
                raise ValueError("Pilot control parent state is invalid JSON") from exc
            if not isinstance(parent_state, dict):
                raise ValueError("Pilot control parent state must contain a JSON object")
            if not verify_payload_signature(parent_state, secret):
                raise ValueError("Pilot control parent state signature is invalid")
            require_state_chain(parent_state)
            if (
                state.get("signature") != parent_state.get("signature")
                or state.get("revision") != parent_state.get("revision")
                or state.get("state_history_sha256")
                != parent_state.get("state_history_sha256")
            ):
                raise ValueError("Pilot control state changed concurrently before update")
            previous_hash = signed_state_sha256(parent_state)
            state["state_history_sha256"] = [
                *list(parent_state["state_history_sha256"]),
                previous_hash,
            ]
            state["revision"] = int(parent_state["revision"]) + 1
        else:
            require_state_chain(state)
            if state.get("revision") != 1 or state.get("state_history_sha256") != []:
                raise ValueError("Initial pilot control state lineage is invalid")
            if path.exists() and not allow_replace:
                raise ValueError("Pilot control state appeared concurrently before initialization")
        state["updated_at"] = utc_timestamp()
        _apply_report(state, report)
        signed_state = sign_payload(state, secret)
        state.clear()
        state.update(signed_state)
        _atomic_write_text(path, json.dumps(state, ensure_ascii=False, indent=2) + "\\n")
        _atomic_write_text(path.with_name("live_pilot_summary.md"), render_markdown(state, report))
'''
text = text[:start] + new_save + text[end:]
text = text.replace(
    "    persist: bool = True,\n) -> int:\n"
    "    report = validate_state(state, final=final)\n"
    "    if persist:\n"
    "        save_state(path, state, report, secret=secret)\n",
    "    persist: bool = True,\n"
    "    allow_replace: bool = False,\n"
    ") -> int:\n"
    "    report = validate_state(state, final=final)\n"
    "    if persist:\n"
    "        save_state(\n"
    "            path, state, report, secret=secret, allow_replace=allow_replace\n"
    "        )\n",
    1,
)
text = text.replace(
    "    return _finish(path, new_state(args.admission_binding), secret=args.signing_secret)",
    "    return _finish(\n"
    "        path,\n"
    "        new_state(args.admission_binding),\n"
    "        secret=args.signing_secret,\n"
    "        allow_replace=args.force,\n"
    "    )",
    1,
)
control.write_text(text, encoding="utf-8")

# Keep the persistent lock inode local and outside source control.
gitignore = Path(".gitignore")
text = gitignore.read_text(encoding="utf-8")
text = text.replace(
    "docs/pilot/live_pilot_summary.md\n",
    "docs/pilot/live_pilot_summary.md\n"
    "docs/pilot/live_pilot_state.json.lock\n",
    1,
)
gitignore.write_text(text, encoding="utf-8")

# True cross-process concurrency and lock-timeout coverage.
tests = Path("backend/tests/test_pilot_control_signature.py")
text = tests.read_text(encoding="utf-8")
text = text.replace(
    "import json\nfrom pathlib import Path\nimport sys\n",
    "import json\nimport multiprocessing\nfrom pathlib import Path\nimport sys\n",
    1,
)
text = text.replace(
    "from pilot_evidence import verify_payload_signature  # noqa: E402\n",
    "from pilot_evidence import verify_payload_signature  # noqa: E402\n"
    "from pilot_control_lock import exclusive_state_lock  # noqa: E402\n",
    1,
)
worker_anchor = "\n\ndef _signed_state(path: Path) -> dict:\n"
workers = '''

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
'''
if text.count(worker_anchor) != 1:
    raise SystemExit("signature test worker anchor changed unexpectedly")
text = text.replace(worker_anchor, workers + worker_anchor, 1)
text += '''


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
'''
tests.write_text(text, encoding="utf-8")

# Immutable release capability v13.
capability = Path("scripts/pilot_release_capability.py")
text = capability.read_text(encoding="utf-8")
text = text.replace(
    '    "scripts/pilot_control_chain.py",\n'
    '    "scripts/pilot_control.py",\n',
    '    "scripts/pilot_control_chain.py",\n'
    '    "scripts/pilot_control_lock.py",\n'
    '    "scripts/pilot_control.py",\n',
    1,
)
text = text.replace('(\"CAPABILITY_VERSION = 12\",)', '(\"CAPABILITY_VERSION = 13\",)', 1)
text = text.replace(
    '_require_markers(bundle, files, "scripts/pilot_control.py", ("SCHEMA_VERSION = 4", "def verified_admission_binding(", "def pilot_signing_secret(", "require_state_chain(state)", "previous_hash = signed_state_sha256(parent_state)", "Replay-vulnerable pilot state schema 3 cannot be reused", "persist=False"), errors)',
    '_require_markers(bundle, files, "scripts/pilot_control.py", ("SCHEMA_VERSION = 4", "exclusive_state_lock(path, timeout_seconds=lock_timeout_seconds)", "Pilot control state appeared concurrently before initialization", "previous_hash = signed_state_sha256(parent_state)", "allow_replace=args.force", "persist=False"), errors)',
    1,
)
anchor = '            _require_markers(bundle, files, "scripts/pilot_control_chain.py", ("def signed_state_sha256(", "def validate_anchor_transition(", "pilot control state revision rollback detected", "pilot control state ancestry does not match the armed runtime"), errors)\n'
addition = anchor + (
    '            _require_markers(bundle, files, "scripts/pilot_control_lock.py", '
    '("def exclusive_state_lock(", "fcntl.LOCK_EX | fcntl.LOCK_NB", '
    '"Pilot control state lock acquisition timed out", "os.fchmod(handle.fileno(), 0o600)"), errors)\n'
)
if text.count(anchor) != 1:
    raise SystemExit("capability chain marker anchor changed unexpectedly")
text = text.replace(anchor, addition, 1)
text = text.replace(
    '_require_markers(bundle, files, "backend/tests/test_pilot_control_signature.py", ("test_authorized_write_advances_revision_and_preserves_exact_parent_hash", "test_replayed_or_unrelated_signed_branch_is_rejected_by_anchor", "test_wrong_secret_and_legacy_schemas_are_rejected"), errors)',
    '_require_markers(bundle, files, "backend/tests/test_pilot_control_signature.py", ("test_cross_process_writers_serialize_and_reject_stale_parent", "test_cross_process_lock_timeout_fails_closed", "multiprocessing.get_context(\\"fork\\")"), errors)',
    1,
)
capability.write_text(text, encoding="utf-8")

capability_tests = Path("backend/tests/test_pilot_release_capability.py")
text = capability_tests.read_text(encoding="utf-8")
text = text.replace("CAPABILITY_VERSION = 12", "CAPABILITY_VERSION = 13")
text = text.replace("assert CAPABILITY_VERSION == 12", "assert CAPABILITY_VERSION == 13")
old_control_fixture = '''    "scripts/pilot_control.py": (
        "SCHEMA_VERSION = 4\\ndef verified_admission_binding(): pass\\n"
        "def pilot_signing_secret(): pass\\n"
        "require_state_chain(state)\\n"
        "previous_hash = signed_state_sha256(parent_state)\\n"
        "Replay-vulnerable pilot state schema 3 cannot be reused\\n"
        "persist=False\\n"
    ),
'''
new_control_fixture = '''    "scripts/pilot_control.py": (
        "SCHEMA_VERSION = 4\\n"
        "exclusive_state_lock(path, timeout_seconds=lock_timeout_seconds)\\n"
        "Pilot control state appeared concurrently before initialization\\n"
        "previous_hash = signed_state_sha256(parent_state)\\n"
        "allow_replace=args.force\\npersist=False\\n"
    ),
'''
if text.count(old_control_fixture) != 1:
    raise SystemExit("capability pilot control fixture changed unexpectedly")
text = text.replace(old_control_fixture, new_control_fixture, 1)
chain_fixture = '''    "scripts/pilot_control_chain.py": (
        "def signed_state_sha256(): pass\\n"
        "def validate_anchor_transition(): pass\\n"
        "pilot control state revision rollback detected\\n"
        "pilot control state ancestry does not match the armed runtime\\n"
    ),
'''
lock_fixture = chain_fixture + '''    "scripts/pilot_control_lock.py": (
        "def exclusive_state_lock(): pass\\n"
        "fcntl.LOCK_EX | fcntl.LOCK_NB\\n"
        "Pilot control state lock acquisition timed out\\n"
        "os.fchmod(handle.fileno(), 0o600)\\n"
    ),
'''
if text.count(chain_fixture) != 1:
    raise SystemExit("capability chain fixture changed unexpectedly")
text = text.replace(chain_fixture, lock_fixture, 1)
old_test_fixture = '''    "backend/tests/test_pilot_control_signature.py": (
        "test_authorized_write_advances_revision_and_preserves_exact_parent_hash\\n"
        "test_replayed_or_unrelated_signed_branch_is_rejected_by_anchor\\n"
        "test_wrong_secret_and_legacy_schemas_are_rejected\\n"
    ),
'''
new_test_fixture = '''    "backend/tests/test_pilot_control_signature.py": (
        "test_cross_process_writers_serialize_and_reject_stale_parent\\n"
        "test_cross_process_lock_timeout_fails_closed\\n"
        'multiprocessing.get_context("fork")\\n'
    ),
'''
if text.count(old_test_fixture) != 1:
    raise SystemExit("capability control signature fixture changed unexpectedly")
capability_tests.write_text(text.replace(old_test_fixture, new_test_fixture, 1), encoding="utf-8")

repository_test = Path("backend/tests/test_pilot_release_capability_repository.py")
text = repository_test.read_text(encoding="utf-8").replace("v12", "v13")
repository_test.write_text(text, encoding="utf-8")

# Operator contract and E2E matrix.
runbook = Path("docs/pilot/admission_bound_state_migration.md")
text = runbook.read_text(encoding="utf-8")
text = text.replace(
    "Authorized record changes reread and verify the exact parent file, append its SHA-256, increment the revision and write a new signature. A second writer holding a stale parent is rejected instead of creating a competing signed branch.",
    "Authorized record changes hold a cross-process exclusive lock while they reread and verify the exact parent file, append its SHA-256, increment the revision, and atomically write both state and summary. A second writer waits for the lock and is then rejected as stale instead of creating or overwriting a competing signed branch. Lock acquisition has a bounded timeout and fails closed.",
)
text = text.replace(
    "- concurrent parent-state replacement;\n",
    "- concurrent parent-state replacement;\n- pilot state lock acquisition timeout;\n",
)
runbook.write_text(text, encoding="utf-8")

matrix = Path("docs/pilot/end_to_end_coverage_matrix.md")
text = matrix.read_text(encoding="utf-8")
text = text.replace(
    "| Runtime pilot guard | Allowlist -> signed live gate -> signed admission -> replay-resistant 20-scenario lineage -> first 20 orders -> automatic STOP | Capability v12, state lineage/DB-anchor/admission/runtime tests and circuit-breaker tests | PASS | Requires fresh signed provider/live evidence and controlled live run |",
    "| Runtime pilot guard | Allowlist -> signed live gate -> signed admission -> locked replay-resistant 20-scenario lineage -> first 20 orders -> automatic STOP | Capability v13, cross-process lock/lineage/DB-anchor/admission/runtime tests and circuit-breaker tests | PASS | Requires fresh signed provider/live evidence and controlled live run |",
)
text = text.replace(
    "PostgreSQL anchors the last accepted revision/hash, so replaying an older valid signature or presenting an unrelated signed branch is also rejected.",
    "PostgreSQL anchors the last accepted revision/hash, so replaying an older valid signature or presenting an unrelated signed branch is also rejected. State mutations are serialized by an OS advisory lock; true cross-process tests prove that only one competing writer succeeds and the stale writer fails closed.",
)
matrix.write_text(text, encoding="utf-8")
