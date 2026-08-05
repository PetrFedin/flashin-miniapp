import json
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
    new_state as _new_state,
    refresh_summary as _refresh_summary,
    save_state as _save_state,
    validate_state,
)
from pilot_control_chain import state_anchor  # noqa: E402
from pilot_control_audit import build_audit_entry, normalize_mutation  # noqa: E402
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
APPROVALS = {
    "business_owner": "Business",
    "operations_owner": "Operations",
    "technical_owner": "Technical",
    "legal_owner": "Legal",
    "support_owner": "Support",
}


def _init_audit():
    mutation = normalize_mutation(
        operation="init",
        operator_role="operations_owner",
        operator_name="Operations",
        reason="Initialize controlled pilot state",
        approvals=APPROVALS,
    )
    return build_audit_entry(mutation, revision=1, parent_state_sha256=None)


def new_state(binding):
    return _new_state(binding, initial_audit=_init_audit())


def _mutation_for_state(path: Path, state: dict):
    parent = json.loads(path.read_text(encoding="utf-8"))
    changed = [
        index + 1
        for index, (before, after) in enumerate(zip(parent["scenarios"], state["scenarios"]))
        if before != after
    ]
    number = changed[0] if len(changed) == 1 else 1
    return normalize_mutation(
        operation="record",
        operator_role="operations_owner",
        operator_name="Operations",
        reason=f"Record verified outcome for scenario {number}",
        approvals=APPROVALS,
        scenario_number=number,
        result=state["scenarios"][number - 1]["result"],
    )


def save_state(path, state, report, *, secret, **kwargs):
    mutation = _mutation_for_state(Path(path), state) if "signature" in state else None
    return _save_state(
        Path(path),
        state,
        report,
        secret=secret,
        approved_operator_names=APPROVALS,
        mutation=mutation,
        **kwargs,
    )


def refresh_summary(path, *, expected_admission, secret, **kwargs):
    return _refresh_summary(
        Path(path),
        expected_admission=expected_admission,
        secret=secret,
        approved_operator_names=APPROVALS,
        **kwargs,
    )


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
    durable_atomic_write_text(target, "durable\n")

    assert target.read_text(encoding="utf-8") == "durable\n"
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
