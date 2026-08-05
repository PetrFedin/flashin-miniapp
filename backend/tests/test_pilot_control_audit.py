import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot_control import build_parser, load_state, new_state, save_state, validate_state  # noqa: E402
from pilot_control_audit import (  # noqa: E402
    build_audit_entry,
    normalize_mutation,
    validate_audit_log,
)
from pilot_control_chain import state_anchor  # noqa: E402

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
    "business_owner": "Business Owner",
    "operations_owner": "Operations Owner",
    "technical_owner": "Technical Owner",
    "legal_owner": "Legal Owner",
    "support_owner": "Support Owner",
}


def init_audit(*, force_reset: bool = False):
    mutation = normalize_mutation(
        operation="init",
        operator_role="operations_owner",
        operator_name="Operations Owner",
        reason="Initialize the controlled pilot state",
        approvals=APPROVALS,
        force_reset=force_reset,
    )
    return build_audit_entry(mutation, revision=1, parent_state_sha256=None)


def record_mutation(number: int, result: str):
    return normalize_mutation(
        operation="record",
        operator_role="operations_owner",
        operator_name="Operations Owner",
        reason=f"Record verified outcome for scenario {number}",
        approvals=APPROVALS,
        scenario_number=number,
        result=result,
    )


def test_init_and_record_are_bound_to_admission_owners_and_lineage(tmp_path: Path):
    path = tmp_path / "live_pilot_state.json"
    state = new_state(BINDING, initial_audit=init_audit())
    save_state(
        path,
        state,
        validate_state(state, final=False),
        secret=SECRET,
        approved_operator_names=APPROVALS,
    )
    parent = state_anchor(state)
    state["scenarios"][0]["result"] = "running"
    save_state(
        path,
        state,
        validate_state(state, final=False),
        secret=SECRET,
        approved_operator_names=APPROVALS,
        mutation=record_mutation(1, "running"),
    )

    assert state["revision"] == 2
    assert len(state["audit_log"]) == 2
    assert state["audit_log"][0]["operation"] == "init"
    assert state["audit_log"][1]["operator_role"] == "operations_owner"
    assert state["audit_log"][1]["operator_name"] == "Operations Owner"
    assert state["audit_log"][1]["scenario_number"] == 1
    assert state["audit_log"][1]["parent_state_sha256"] == parent["sha256"]
    assert validate_audit_log(state, approvals=APPROVALS) == []


def test_unapproved_name_or_role_is_rejected():
    with pytest.raises(ValueError, match="does not match signed admission owner"):
        normalize_mutation(
            operation="record",
            operator_role="operations_owner",
            operator_name="Someone Else",
            reason="Record verified scenario outcome",
            approvals=APPROVALS,
            scenario_number=1,
            result="pass",
        )


def test_misleading_scenario_audit_is_rejected(tmp_path: Path):
    path = tmp_path / "live_pilot_state.json"
    state = new_state(BINDING, initial_audit=init_audit())
    save_state(
        path,
        state,
        validate_state(state, final=False),
        secret=SECRET,
        approved_operator_names=APPROVALS,
    )
    state["scenarios"][0]["result"] = "running"
    with pytest.raises(ValueError, match="audit scenario does not match"):
        save_state(
            path,
            state,
            validate_state(state, final=False),
            secret=SECRET,
            approved_operator_names=APPROVALS,
            mutation=record_mutation(2, "running"),
        )


def test_tampered_or_unapproved_audit_fails_state_load(tmp_path: Path):
    path = tmp_path / "live_pilot_state.json"
    state = new_state(BINDING, initial_audit=init_audit())
    save_state(
        path,
        state,
        validate_state(state, final=False),
        secret=SECRET,
        approved_operator_names=APPROVALS,
    )
    changed_approvals = dict(APPROVALS)
    changed_approvals["operations_owner"] = "Replacement Owner"
    with pytest.raises(ValueError, match="does not match signed admission owner"):
        load_state(
            path,
            expected_admission=BINDING,
            secret=SECRET,
            approved_operator_names=changed_approvals,
        )


def test_init_and_record_parser_require_accountable_identity():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["init"])
    with pytest.raises(SystemExit):
        parser.parse_args(["record", "--number", "1", "--result", "pass"])
