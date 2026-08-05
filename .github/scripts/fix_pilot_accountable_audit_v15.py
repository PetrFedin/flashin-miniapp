from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one occurrence, found {count}: {old[:160]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Reject a stale writer before interpreting its mutation against a newer parent.
replace_once(
    "scripts/pilot_control.py",
    '''            if mutation is None:
                raise ValueError("Pilot record mutation audit metadata is required")
            mutation_errors = validate_record_mutation(parent_state, state, mutation)
            if mutation_errors:
                raise ValueError("; ".join(mutation_errors))
            if (
                state.get("signature") != parent_state.get("signature")
                or state.get("revision") != parent_state.get("revision")
                or state.get("state_history_sha256")
                != parent_state.get("state_history_sha256")
                or state.get("audit_log") != parent_state.get("audit_log")
            ):
                raise ValueError("Pilot control state changed concurrently before update")
''',
    '''            if mutation is None:
                raise ValueError("Pilot record mutation audit metadata is required")
            if (
                state.get("signature") != parent_state.get("signature")
                or state.get("revision") != parent_state.get("revision")
                or state.get("state_history_sha256")
                != parent_state.get("state_history_sha256")
                or state.get("audit_log") != parent_state.get("audit_log")
            ):
                raise ValueError("Pilot control state changed concurrently before update")
            mutation_errors = validate_record_mutation(parent_state, state, mutation)
            if mutation_errors:
                raise ValueError("; ".join(mutation_errors))
''',
)

# Runtime descendant fixtures must carry a structurally truthful audit entry.
runtime_path = Path("backend/tests/test_pilot_runtime.py")
text = runtime_path.read_text(encoding="utf-8")
old_helper = '''def _next_signed_state(payload: dict, secret: str) -> dict:
    parent = state_anchor(payload)
    child = dict(payload)
    child.pop("signature", None)
    child["revision"] = parent["revision"] + 1
    child["state_history_sha256"] = [*parent["history"], parent["sha256"]]
    return sign_payload(child, secret)
'''
new_helper = '''def _next_signed_state(
    payload: dict,
    secret: str,
    *,
    scenario_number: int = 1,
    result: str = "running",
    decision: str | None = None,
    admission_sha256: str | None = None,
    parent_sha256: str | None = None,
) -> dict:
    parent = state_anchor(payload)
    child = json.loads(json.dumps(payload))
    child.pop("signature", None)
    effective_parent = parent_sha256 or parent["sha256"]
    child["revision"] = parent["revision"] + 1
    child["state_history_sha256"] = [*parent["history"], effective_parent]
    child["scenarios"][scenario_number - 1]["result"] = result
    if decision is not None:
        child["decision"] = decision
    if admission_sha256 is not None:
        child["admission"]["manifest_sha256"] = admission_sha256
    approvals = {
        "business_owner": "Business",
        "operations_owner": "Operations",
        "technical_owner": "Technical",
        "legal_owner": "Legal",
        "support_owner": "Support",
    }
    mutation = normalize_mutation(
        operation="record",
        operator_role="operations_owner",
        operator_name="Operations",
        reason=f"Record verified outcome for scenario {scenario_number}",
        approvals=approvals,
        scenario_number=scenario_number,
        result=result,
    )
    child["audit_log"] = [
        *list(payload["audit_log"]),
        build_audit_entry(
            mutation,
            revision=child["revision"],
            parent_state_sha256=effective_parent,
        ),
    ]
    return sign_payload(child, secret)
'''
if text.count(old_helper) != 1:
    raise SystemExit("runtime descendant helper changed unexpectedly")
text = text.replace(old_helper, new_helper, 1)

old_stop = '''    payload = json.loads(pilot_path.read_text(encoding="utf-8"))
    payload["decision"] = "STOP"
    pilot_path.write_text(
        json.dumps(_next_signed_state(payload, env["PILOT_EVIDENCE_SIGNING_SECRET"])),
        encoding="utf-8",
    )
'''
new_stop = '''    payload = json.loads(pilot_path.read_text(encoding="utf-8"))
    pilot_path.write_text(
        json.dumps(
            _next_signed_state(
                payload,
                env["PILOT_EVIDENCE_SIGNING_SECRET"],
                result="fail",
                decision="STOP",
            )
        ),
        encoding="utf-8",
    )
'''
if text.count(old_stop) != 1:
    raise SystemExit("runtime STOP fixture changed unexpectedly")
text = text.replace(old_stop, new_stop, 1)

old_admission = '''    payload = json.loads(pilot_path.read_text(encoding="utf-8"))
    payload["admission"]["manifest_sha256"] = "0" * 64
    pilot_path.write_text(
        json.dumps(_next_signed_state(payload, env["PILOT_EVIDENCE_SIGNING_SECRET"])),
        encoding="utf-8",
    )
'''
new_admission = '''    payload = json.loads(pilot_path.read_text(encoding="utf-8"))
    pilot_path.write_text(
        json.dumps(
            _next_signed_state(
                payload,
                env["PILOT_EVIDENCE_SIGNING_SECRET"],
                admission_sha256="0" * 64,
            )
        ),
        encoding="utf-8",
    )
'''
if text.count(old_admission) != 1:
    raise SystemExit("runtime admission mismatch fixture changed unexpectedly")
text = text.replace(old_admission, new_admission, 1)

old_descendant = '''    descendant = _next_signed_state(original, env["PILOT_EVIDENCE_SIGNING_SECRET"])
    descendant["scenarios"][0]["result"] = "running"
    descendant = sign_payload(descendant, env["PILOT_EVIDENCE_SIGNING_SECRET"])
'''
new_descendant = '''    descendant = _next_signed_state(
        original, env["PILOT_EVIDENCE_SIGNING_SECRET"]
    )
'''
if text.count(old_descendant) != 1:
    raise SystemExit("runtime descendant test changed unexpectedly")
text = text.replace(old_descendant, new_descendant, 1)

old_fork = '''    fork = dict(current)
    fork.pop("signature", None)
    fork["revision"] = 2
    fork["state_history_sha256"] = ["f" * 64]
    fork = sign_payload(fork, env["PILOT_EVIDENCE_SIGNING_SECRET"])
'''
new_fork = '''    fork = _next_signed_state(
        current,
        env["PILOT_EVIDENCE_SIGNING_SECRET"],
        parent_sha256="f" * 64,
    )
'''
if text.count(old_fork) != 1:
    raise SystemExit("runtime unrelated fork fixture changed unexpectedly")
text = text.replace(old_fork, new_fork, 1)
runtime_path.write_text(text, encoding="utf-8")
