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
    "CAPABILITY_VERSION = 14",
    "CAPABILITY_VERSION = 15",
)

Path("scripts/pilot_control_audit.py").write_text(
    '''"""Accountable append-only audit entries for signed pilot control states."""

from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import Any, Mapping
import uuid

from script_time import utc_timestamp

APPROVAL_ROLES = (
    "business_owner",
    "operations_owner",
    "technical_owner",
    "legal_owner",
    "support_owner",
)
_ALLOWED_OPERATIONS = {"init", "record"}
_ALLOWED_RESULTS = {"running", "pass", "fail", "blocked"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def approved_operators(manifest: Mapping[str, Any]) -> dict[str, str]:
    approvals = manifest.get("approvals")
    if not isinstance(approvals, Mapping):
        raise ValueError("Pilot admission approvals are missing")
    normalized = {role: str(approvals.get(role, "")).strip() for role in APPROVAL_ROLES}
    missing = [role for role, name in normalized.items() if not name]
    if missing:
        raise ValueError("Pilot admission owner is missing: " + ", ".join(missing))
    return normalized


def require_approved_operator(
    approvals: Mapping[str, str],
    operator_role: object,
    operator_name: object,
) -> tuple[str, str]:
    role = str(operator_role or "").strip()
    name = str(operator_name or "").strip()
    if role not in APPROVAL_ROLES:
        raise ValueError("Pilot mutation operator role is not admission-approved")
    expected = str(approvals.get(role, "")).strip()
    if not expected:
        raise ValueError(f"Pilot admission owner is missing: {role}")
    if name != expected:
        raise ValueError(
            f"Pilot mutation operator does not match signed admission owner for {role}"
        )
    return role, name


def _clean_reason(value: object) -> str:
    reason = str(value or "").strip()
    if len(reason) < 5 or len(reason) > 500:
        raise ValueError("Pilot mutation reason must contain 5 to 500 characters")
    if any(ord(character) < 32 and character not in "\t" for character in reason):
        raise ValueError("Pilot mutation reason contains control characters")
    return reason


def normalize_mutation(
    *,
    operation: object,
    operator_role: object,
    operator_name: object,
    reason: object,
    approvals: Mapping[str, str],
    scenario_number: object = None,
    result: object = None,
    force_reset: bool = False,
) -> dict[str, Any]:
    op = str(operation or "").strip()
    if op not in _ALLOWED_OPERATIONS:
        raise ValueError("Pilot mutation operation is invalid")
    role, name = require_approved_operator(approvals, operator_role, operator_name)
    normalized: dict[str, Any] = {
        "operation": op,
        "operator_role": role,
        "operator_name": name,
        "reason": _clean_reason(reason),
        "force_reset": bool(force_reset),
    }
    if op == "init":
        if scenario_number is not None or result is not None:
            raise ValueError("Pilot init audit cannot reference a scenario result")
        normalized.update({"scenario_number": None, "result": None})
    else:
        if type(scenario_number) is not int or not 1 <= scenario_number <= 20:
            raise ValueError("Pilot record audit scenario number must be between 1 and 20")
        normalized_result = str(result or "").strip()
        if normalized_result not in _ALLOWED_RESULTS:
            raise ValueError("Pilot record audit result is invalid")
        normalized.update(
            {"scenario_number": scenario_number, "result": normalized_result}
        )
        if force_reset:
            raise ValueError("Pilot record audit cannot be marked as a force reset")
    return normalized


def build_audit_entry(
    mutation: Mapping[str, Any],
    *,
    revision: int,
    parent_state_sha256: str | None,
    changed_at: str | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    timestamp = changed_at or utc_timestamp()
    entry = {
        "event_id": event_id or str(uuid.uuid4()),
        "revision": revision,
        "operation": mutation.get("operation"),
        "operator_role": mutation.get("operator_role"),
        "operator_name": mutation.get("operator_name"),
        "reason": mutation.get("reason"),
        "changed_at": timestamp,
        "parent_state_sha256": parent_state_sha256,
        "scenario_number": mutation.get("scenario_number"),
        "result": mutation.get("result"),
        "force_reset": mutation.get("force_reset") is True,
    }
    errors = _validate_entry(entry, revision=revision, expected_parent=parent_state_sha256)
    if errors:
        raise ValueError("; ".join(errors))
    return entry


def _valid_timestamp(value: object) -> bool:
    raw = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.astimezone(UTC).utcoffset() is not None


def _validate_entry(
    entry: object,
    *,
    revision: int,
    expected_parent: str | None,
    approvals: Mapping[str, str] | None = None,
) -> list[str]:
    if not isinstance(entry, Mapping):
        return [f"pilot audit entry #{revision} must be an object"]
    errors: list[str] = []
    try:
        uuid.UUID(str(entry.get("event_id", "")))
    except ValueError:
        errors.append(f"pilot audit entry #{revision} event ID is invalid")
    if entry.get("revision") != revision:
        errors.append(f"pilot audit entry #{revision} revision does not match")
    operation = entry.get("operation")
    expected_operation = "init" if revision == 1 else "record"
    if operation != expected_operation:
        errors.append(f"pilot audit entry #{revision} operation must be {expected_operation}")
    if entry.get("parent_state_sha256") != expected_parent:
        errors.append(f"pilot audit entry #{revision} parent SHA-256 does not match lineage")
    if not _valid_timestamp(entry.get("changed_at")):
        errors.append(f"pilot audit entry #{revision} timestamp is invalid")
    try:
        _clean_reason(entry.get("reason"))
    except ValueError as exc:
        errors.append(f"pilot audit entry #{revision}: {exc}")
    role = str(entry.get("operator_role", "")).strip()
    name = str(entry.get("operator_name", "")).strip()
    if role not in APPROVAL_ROLES or not name:
        errors.append(f"pilot audit entry #{revision} operator is invalid")
    elif approvals is not None:
        try:
            require_approved_operator(approvals, role, name)
        except ValueError as exc:
            errors.append(f"pilot audit entry #{revision}: {exc}")
    if revision == 1:
        if entry.get("scenario_number") is not None or entry.get("result") is not None:
            errors.append("pilot audit init entry cannot reference a scenario")
        if type(entry.get("force_reset")) is not bool:
            errors.append("pilot audit init force_reset must be boolean")
    else:
        number = entry.get("scenario_number")
        if type(number) is not int or not 1 <= number <= 20:
            errors.append(f"pilot audit entry #{revision} scenario number is invalid")
        if entry.get("result") not in _ALLOWED_RESULTS:
            errors.append(f"pilot audit entry #{revision} result is invalid")
        if entry.get("force_reset") is not False:
            errors.append(f"pilot audit entry #{revision} cannot be a force reset")
    return errors


def validate_audit_log(
    state: Mapping[str, Any],
    *,
    approvals: Mapping[str, str] | None = None,
) -> list[str]:
    revision = state.get("revision")
    history = state.get("state_history_sha256")
    audit = state.get("audit_log")
    if type(revision) is not int or revision < 1:
        return ["pilot audit cannot validate an invalid state revision"]
    if not isinstance(history, list):
        return ["pilot audit cannot validate an invalid state history"]
    if not isinstance(audit, list):
        return ["pilot audit log must be a list"]
    errors: list[str] = []
    if len(audit) != revision:
        errors.append("pilot audit log length does not match state revision")
    if len(audit) > 1000:
        errors.append("pilot audit log exceeds the controlled pilot limit")
    event_ids: list[str] = []
    for current_revision, entry in enumerate(audit, start=1):
        expected_parent = None if current_revision == 1 else (
            history[current_revision - 2]
            if current_revision - 2 < len(history)
            else ""
        )
        errors.extend(
            _validate_entry(
                entry,
                revision=current_revision,
                expected_parent=expected_parent,
                approvals=approvals,
            )
        )
        if isinstance(entry, Mapping):
            event_ids.append(str(entry.get("event_id", "")))
    if len(event_ids) != len(set(event_ids)):
        errors.append("pilot audit log contains duplicate event IDs")
    return list(dict.fromkeys(errors))


def require_audit_log(
    state: Mapping[str, Any],
    *,
    approvals: Mapping[str, str] | None = None,
) -> None:
    errors = validate_audit_log(state, approvals=approvals)
    if errors:
        raise ValueError("; ".join(errors))


def validate_record_mutation(
    parent_state: Mapping[str, Any],
    candidate_state: Mapping[str, Any],
    mutation: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    parent_records = parent_state.get("scenarios")
    candidate_records = candidate_state.get("scenarios")
    if not isinstance(parent_records, list) or not isinstance(candidate_records, list):
        return ["pilot mutation requires scenario lists"]
    changed = [
        index + 1
        for index, (before, after) in enumerate(zip(parent_records, candidate_records))
        if before != after
    ]
    if len(changed) != 1:
        errors.append("pilot record mutation must change exactly one scenario")
    else:
        number = changed[0]
        if mutation.get("scenario_number") != number:
            errors.append("pilot audit scenario does not match the changed scenario")
        after = candidate_records[number - 1]
        if not isinstance(after, Mapping) or mutation.get("result") != after.get("result"):
            errors.append("pilot audit result does not match the changed scenario")
    immutable_keys = set(parent_state) | set(candidate_state)
    immutable_keys.discard("scenarios")
    for key in immutable_keys:
        if candidate_state.get(key) != parent_state.get(key):
            errors.append(f"pilot record mutation changed protected state field: {key}")
    return list(dict.fromkeys(errors))
''',
    encoding="utf-8",
)

# Chain validation now includes the append-only audit structure.
chain = Path("scripts/pilot_control_chain.py")
text = chain.read_text(encoding="utf-8")
text = text.replace(
    "from typing import Any, Mapping, Sequence\n",
    "from typing import Any, Mapping, Sequence\n\n"
    "from pilot_control_audit import validate_audit_log\n",
    1,
)
text = text.replace(
    'def validate_state_chain(state: Mapping[str, Any]) -> list[str]:\n'
    '    return validate_chain_fields(state.get("revision"), state.get("state_history_sha256"))\n',
    'def validate_state_chain(state: Mapping[str, Any]) -> list[str]:\n'
    '    errors = validate_chain_fields(\n'
    '        state.get("revision"), state.get("state_history_sha256")\n'
    '    )\n'
    '    if not errors:\n'
    '        errors.extend(validate_audit_log(state))\n'
    '    return list(dict.fromkeys(errors))\n',
    1,
)
chain.write_text(text, encoding="utf-8")

# Accountable schema v5 and explicit admission-owner mutations.
control = Path("scripts/pilot_control.py")
text = control.read_text(encoding="utf-8")
text = text.replace(
    "from pilot_control_binding import build_admission_binding, require_admission_binding\n",
    "from pilot_control_audit import (\n"
    "    APPROVAL_ROLES,\n"
    "    approved_operators,\n"
    "    build_audit_entry,\n"
    "    normalize_mutation,\n"
    "    require_audit_log,\n"
    "    validate_record_mutation,\n"
    ")\n"
    "from pilot_control_binding import build_admission_binding, require_admission_binding\n",
    1,
)
text = text.replace("SCHEMA_VERSION = 4", "SCHEMA_VERSION = 5", 1)
old_verified = '''def verified_admission_binding(root: Path = ROOT) -> dict[str, Any]:
    from pilot_admission import verify_default_admission
    from pilot_evidence import load_json

    errors = verify_default_admission(root)
    if errors:
        raise ValueError("Pilot admission is invalid: " + "; ".join(errors))
    manifest_path = root / DEFAULT_MANIFEST_PATH
    return build_admission_binding(manifest_path, load_json(manifest_path))
'''
new_verified = '''def verified_admission_context(
    root: Path = ROOT,
) -> tuple[dict[str, Any], dict[str, str]]:
    from pilot_admission import verify_default_admission
    from pilot_evidence import load_json

    errors = verify_default_admission(root)
    if errors:
        raise ValueError("Pilot admission is invalid: " + "; ".join(errors))
    manifest_path = root / DEFAULT_MANIFEST_PATH
    manifest = load_json(manifest_path)
    return build_admission_binding(manifest_path, manifest), approved_operators(manifest)


def verified_admission_binding(root: Path = ROOT) -> dict[str, Any]:
    return verified_admission_context(root)[0]
'''
if text.count(old_verified) != 1:
    raise SystemExit("pilot_control verified admission block changed unexpectedly")
text = text.replace(old_verified, new_verified, 1)
text = text.replace(
    "def new_state(admission_binding: Mapping[str, Any]) -> dict[str, Any]:\n"
    "    now = utc_timestamp()\n",
    "def new_state(\n"
    "    admission_binding: Mapping[str, Any],\n"
    "    *,\n"
    "    initial_audit: Mapping[str, Any],\n"
    ") -> dict[str, Any]:\n"
    "    now = str(initial_audit.get(\"changed_at\", \"\")).strip() or utc_timestamp()\n",
    1,
)
text = text.replace(
    '        "state_history_sha256": [],\n'
    '        "admission": json.loads(json.dumps(dict(admission_binding))),\n',
    '        "state_history_sha256": [],\n'
    '        "audit_log": [json.loads(json.dumps(dict(initial_audit)))],\n'
    '        "admission": json.loads(json.dumps(dict(admission_binding))),\n',
    1,
)
text = text.replace(
    '    _apply_report(state, validate_state(state, final=False))\n'
    '    return state\n',
    '    require_audit_log(state)\n'
    '    _apply_report(state, validate_state(state, final=False))\n'
    '    return state\n',
    1,
)
old_schema = '''    if schema == 3:
        raise ValueError(
            "Replay-vulnerable pilot state schema 3 cannot be reused. Archive it and "
            "initialize a fresh replay-resistant pilot state."
        )
    if schema != SCHEMA_VERSION:
'''
new_schema = '''    if schema == 3:
        raise ValueError(
            "Replay-vulnerable pilot state schema 3 cannot be reused. Archive it and "
            "initialize a fresh replay-resistant pilot state."
        )
    if schema == 4:
        raise ValueError(
            "Unattributed pilot state schema 4 cannot be reused. Archive it and "
            "initialize a fresh accountable pilot state."
        )
    if schema != SCHEMA_VERSION:
'''
if text.count(old_schema) != 1:
    raise SystemExit("pilot_control schema block changed unexpectedly")
text = text.replace(old_schema, new_schema, 1)
text = text.replace(
    "    expected_admission: Mapping[str, Any] | None = None,\n"
    "    secret: str | None = None,\n",
    "    expected_admission: Mapping[str, Any] | None = None,\n"
    "    secret: str | None = None,\n"
    "    approved_operator_names: Mapping[str, str] | None = None,\n",
    1,
)
text = text.replace(
    "    require_state_chain(state)\n"
    "    if [item.get(\"number\") for item in _scenario_records(state)]",
    "    require_state_chain(state)\n"
    "    if approved_operator_names is not None:\n"
    "        require_audit_log(state, approvals=approved_operator_names)\n"
    "    if [item.get(\"number\") for item in _scenario_records(state)]",
    1,
)
text = text.replace(
    "    allow_replace: bool = False,\n"
    "    lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,\n",
    "    allow_replace: bool = False,\n"
    "    lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,\n"
    "    approved_operator_names: Mapping[str, str],\n"
    "    mutation: Mapping[str, Any] | None = None,\n",
    1,
)
text = text.replace(
    "            require_state_chain(parent_state)\n"
    "            if (\n"
    "                state.get(\"signature\") != parent_state.get(\"signature\")\n"
    "                or state.get(\"revision\") != parent_state.get(\"revision\")\n"
    "                or state.get(\"state_history_sha256\")\n"
    "                != parent_state.get(\"state_history_sha256\")\n"
    "            ):\n",
    "            require_state_chain(parent_state)\n"
    "            require_audit_log(parent_state, approvals=approved_operator_names)\n"
    "            if mutation is None:\n"
    "                raise ValueError(\"Pilot record mutation audit metadata is required\")\n"
    "            mutation_errors = validate_record_mutation(parent_state, state, mutation)\n"
    "            if mutation_errors:\n"
    "                raise ValueError(\"; \".join(mutation_errors))\n"
    "            if (\n"
    "                state.get(\"signature\") != parent_state.get(\"signature\")\n"
    "                or state.get(\"revision\") != parent_state.get(\"revision\")\n"
    "                or state.get(\"state_history_sha256\")\n"
    "                != parent_state.get(\"state_history_sha256\")\n"
    "                or state.get(\"audit_log\") != parent_state.get(\"audit_log\")\n"
    "            ):\n",
    1,
)
text = text.replace(
    "            state[\"revision\"] = int(parent_state[\"revision\"]) + 1\n"
    "        else:\n"
    "            require_state_chain(state)\n",
    "            state[\"revision\"] = int(parent_state[\"revision\"]) + 1\n"
    "            changed_at = utc_timestamp()\n"
    "            state[\"audit_log\"] = [\n"
    "                *list(parent_state[\"audit_log\"]),\n"
    "                build_audit_entry(\n"
    "                    mutation,\n"
    "                    revision=int(state[\"revision\"]),\n"
    "                    parent_state_sha256=previous_hash,\n"
    "                    changed_at=changed_at,\n"
    "                ),\n"
    "            ]\n"
    "        else:\n"
    "            require_state_chain(state)\n"
    "            require_audit_log(state, approvals=approved_operator_names)\n"
    "            if mutation is not None:\n"
    "                raise ValueError(\"Pilot init mutation must be embedded in initial audit\")\n",
    1,
)
text = text.replace(
    "        state[\"updated_at\"] = utc_timestamp()\n"
    "        _apply_report(state, report)\n",
    "        state[\"updated_at\"] = (\n"
    "            changed_at if \"signature\" in parent_state if False else state.get(\"updated_at\")\n"
    "        )\n",
    1,
)
# Replace the deliberately temporary invalid line with a branch-safe timestamp block.
text = text.replace(
    '        state["updated_at"] = (\n'
    '            changed_at if "signature" in parent_state if False else state.get("updated_at")\n'
    '        )\n',
    '        if mutation is not None:\n'
    '            state["updated_at"] = changed_at\n'
    '        else:\n'
    '            state["updated_at"] = str(state["audit_log"][0]["changed_at"])\n'
    '        require_audit_log(state, approvals=approved_operator_names)\n'
    '        _apply_report(state, report)\n',
    1,
)
text = text.replace(
    "            expected_admission=expected_admission,\n"
    "            secret=secret,\n",
    "            expected_admission=expected_admission,\n"
    "            secret=secret,\n"
    "            approved_operator_names=approved_operator_names,\n",
    1,
)
text = text.replace(
    "    approved_operator_names: Mapping[str, str] | None = None,\n"
    ") -> dict[str, Any]:",
    "    approved_operator_names: Mapping[str, str] | None = None,\n"
    ") -> dict[str, Any]:",
    1,
)
# refresh_summary signature/context.
text = text.replace(
    "    secret: str,\n"
    "    final: bool = False,\n"
    "    lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,\n"
    ") -> tuple[dict[str, Any], dict[str, Any]]:\n",
    "    secret: str,\n"
    "    approved_operator_names: Mapping[str, str],\n"
    "    final: bool = False,\n"
    "    lock_timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS,\n"
    ") -> tuple[dict[str, Any], dict[str, Any]]:\n",
    1,
)
# _finish signature and save context.
text = text.replace(
    "    allow_replace: bool = False,\n"
    ") -> int:\n",
    "    allow_replace: bool = False,\n"
    "    approved_operator_names: Mapping[str, str],\n"
    "    mutation: Mapping[str, Any] | None = None,\n"
    ") -> int:\n",
    1,
)
text = text.replace(
    "            path, state, report, secret=secret, allow_replace=allow_replace\n"
    "        )\n",
    "            path,\n"
    "            state,\n"
    "            report,\n"
    "            secret=secret,\n"
    "            allow_replace=allow_replace,\n"
    "            approved_operator_names=approved_operator_names,\n"
    "            mutation=mutation,\n"
    "        )\n",
    1,
)
# Commands and mutation identity.
old_init = '''def command_init(args: argparse.Namespace) -> int:
    path = _state_path(args)
    if path.exists() and not args.force:
        raise ValueError(f"Pilot state already exists: {path}. Use --force only for an intentional reset.")
    return _finish(
        path,
        new_state(args.admission_binding),
        secret=args.signing_secret,
        allow_replace=args.force,
    )
'''
new_init = '''def _mutation_from_args(
    args: argparse.Namespace,
    *,
    operation: str,
    scenario_number: int | None = None,
    result: str | None = None,
) -> dict[str, Any]:
    return normalize_mutation(
        operation=operation,
        operator_role=args.operator_role,
        operator_name=args.operator,
        reason=args.reason,
        approvals=args.approved_operators,
        scenario_number=scenario_number,
        result=result,
        force_reset=bool(getattr(args, "force", False)),
    )


def command_init(args: argparse.Namespace) -> int:
    path = _state_path(args)
    if path.exists() and not args.force:
        raise ValueError(f"Pilot state already exists: {path}. Use --force only for an intentional reset.")
    mutation = _mutation_from_args(args, operation="init")
    initial_audit = build_audit_entry(
        mutation,
        revision=1,
        parent_state_sha256=None,
    )
    return _finish(
        path,
        new_state(args.admission_binding, initial_audit=initial_audit),
        secret=args.signing_secret,
        allow_replace=args.force,
        approved_operator_names=args.approved_operators,
    )
'''
if text.count(old_init) != 1:
    raise SystemExit("pilot_control command_init block changed unexpectedly")
text = text.replace(old_init, new_init, 1)
text = text.replace(
    "    return _finish(path, state, secret=args.signing_secret)\n",
    "    return _finish(\n"
    "        path,\n"
    "        state,\n"
    "        secret=args.signing_secret,\n"
    "        approved_operator_names=args.approved_operators,\n"
    "        mutation=_mutation_from_args(\n"
    "            args,\n"
    "            operation=\"record\",\n"
    "            scenario_number=args.number,\n"
    "            result=args.result,\n"
    "        ),\n"
    "    )\n",
    1,
)
text = text.replace(
    "        secret=args.signing_secret,\n"
    "    )\n"
    "    return _report_exit(report, final=False)\n",
    "        secret=args.signing_secret,\n"
    "        approved_operator_names=args.approved_operators,\n"
    "    )\n"
    "    return _report_exit(report, final=False)\n",
    1,
)
text = text.replace(
    "        secret=args.signing_secret,\n"
    "        final=args.final,\n",
    "        secret=args.signing_secret,\n"
    "        approved_operator_names=args.approved_operators,\n"
    "        final=args.final,\n",
    1,
)
parser_anchor = '''    init_parser = subparsers.add_parser("init", help="Create a fresh 20-order pilot state")
    init_parser.add_argument("--force", action="store_true", help="Replace an existing state")
    init_parser.set_defaults(handler=command_init)

    record_parser = subparsers.add_parser("record", help="Record one pilot scenario result")
'''
parser_replacement = '''    def add_mutation_identity(target: argparse.ArgumentParser) -> None:
        target.add_argument("--operator-role", choices=APPROVAL_ROLES, required=True)
        target.add_argument("--operator", required=True)
        target.add_argument("--reason", required=True)

    init_parser = subparsers.add_parser("init", help="Create a fresh 20-order pilot state")
    init_parser.add_argument("--force", action="store_true", help="Replace an existing state")
    add_mutation_identity(init_parser)
    init_parser.set_defaults(handler=command_init)

    record_parser = subparsers.add_parser("record", help="Record one pilot scenario result")
    add_mutation_identity(record_parser)
'''
if text.count(parser_anchor) != 1:
    raise SystemExit("pilot_control parser anchor changed unexpectedly")
text = text.replace(parser_anchor, parser_replacement, 1)
text = text.replace(
    "    if args.command is None:\n"
    "        path = Path(args.state)\n"
    "        return main([\"--state\", str(path), \"status\" if path.exists() else \"init\"])\n",
    "    if args.command is None:\n"
    "        path = Path(args.state)\n"
    "        if path.exists():\n"
    "            return main([\"--state\", str(path), \"status\"])\n"
    "        parser.error(\n"
    "            \"Pilot initialization requires explicit --operator-role, --operator and --reason\"\n"
    "        )\n",
    1,
)
text = text.replace(
    "        args.admission_binding = verified_admission_binding(ROOT)\n"
    "        args.signing_secret = pilot_signing_secret(ROOT)\n",
    "        (\n"
    "            args.admission_binding,\n"
    "            args.approved_operators,\n"
    "        ) = verified_admission_context(ROOT)\n"
    "        args.signing_secret = pilot_signing_secret(ROOT)\n",
    1,
)
# Summary shows the accountable last mutation.
text = text.replace(
    "    state_sha = signed_state_sha256(state)\n"
    "    lines = [\n",
    "    state_sha = signed_state_sha256(state)\n"
    "    last_audit = state[\"audit_log\"][-1]\n"
    "    lines = [\n",
    1,
)
text = text.replace(
    '        f"State SHA-256: `{state_sha}`",\n'
    '        "Source: signed JSON state. This Markdown file is derived and non-authoritative.",\n',
    '        f"State SHA-256: `{state_sha}`",\n'
    '        f"Last accountable mutation: `{last_audit[\'operator_role\']}` / {last_audit[\'operator_name\']}",\n'
    '        f"Mutation reason: {last_audit[\'reason\']}",\n'
    '        "Source: signed JSON state. This Markdown file is derived and non-authoritative.",\n',
    1,
)
control.write_text(text, encoding="utf-8")

# Runtime verifies audit owners against the exact signed admission.
runtime = Path("backend/services/pilot_runtime.py")
text = runtime.read_text(encoding="utf-8")
text = text.replace(
    "from scripts.pilot_control_binding import build_admission_binding, validate_admission_binding\n",
    "from scripts.pilot_control_audit import approved_operators, validate_audit_log\n"
    "from scripts.pilot_control_binding import build_admission_binding, validate_admission_binding\n",
    1,
)
text = text.replace(
    '    if pilot_state.get("schema_version") != 4:\n',
    '    if pilot_state.get("schema_version") != 5:\n',
    1,
)
text = text.replace(
    "        chain_errors = validate_state_chain(pilot_state)\n"
    "        errors.extend(chain_errors)\n",
    "        chain_errors = validate_state_chain(pilot_state)\n"
    "        errors.extend(chain_errors)\n"
    "        try:\n"
    "            errors.extend(\n"
    "                validate_audit_log(\n"
    "                    pilot_state, approvals=approved_operators(manifest)\n"
    "                )\n"
    "            )\n"
    "        except ValueError as exc:\n"
    "            errors.append(str(exc))\n",
    1,
)
runtime.write_text(text, encoding="utf-8")

runtime_cli = Path("scripts/pilot_runtime.py")
text = runtime_cli.read_text(encoding="utf-8")
text = text.replace(
    "from pilot_control_chain import (\n",
    "from pilot_control_audit import approved_operators, validate_audit_log\n"
    "from pilot_control_chain import (\n",
    1,
)
text = text.replace(
    '        if pilot_state.get("schema_version") != 4:\n',
    '        if pilot_state.get("schema_version") != 5:\n',
    1,
)
text = text.replace(
    "        chain_errors = validate_state_chain(pilot_state)\n"
    "        if chain_errors:\n"
    "            raise ValueError(\"; \".join(chain_errors))\n",
    "        chain_errors = validate_state_chain(pilot_state)\n"
    "        if chain_errors:\n"
    "            raise ValueError(\"; \".join(chain_errors))\n"
    "        audit_errors = validate_audit_log(\n"
    "            pilot_state, approvals=approved_operators(manifest)\n"
    "        )\n"
    "        if audit_errors:\n"
    "            raise ValueError(\"; \".join(audit_errors))\n",
    1,
)
runtime_cli.write_text(text, encoding="utf-8")

# Makefile requires explicit accountable identity for writes.
makefile = Path("Makefile")
text = makefile.read_text(encoding="utf-8")
text = text.replace(
    "pilot-init:\n\tpython3 scripts/pilot_runner.py init\n",
    "pilot-init:\n"
    "\t@echo \"Usage: make pilot-init ARGS='--operator-role operations_owner --operator \\\"Name\\\" --reason \\\"Initialize controlled pilot\\\" [--force]'\"\n"
    "\tpython3 scripts/pilot_runner.py init $(ARGS)\n",
    1,
)
text = text.replace(
    "\t@echo \"Usage: make pilot-record ARGS='--number 1 --result pass ...'\"\n",
    "\t@echo \"Usage: make pilot-record ARGS='--number 1 --result pass --operator-role operations_owner --operator \\\"Name\\\" --reason \\\"Verified scenario\\\" ...'\"\n",
    1,
)
makefile.write_text(text, encoding="utf-8")

# Reusable tests for accountable audit behavior.
Path("backend/tests/test_pilot_control_audit.py").write_text(
    '''import json
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
''',
    encoding="utf-8",
)

# Adapt pure control tests with an accountable initial audit wrapper.
control_tests = Path("backend/tests/test_pilot_control.py")
text = control_tests.read_text(encoding="utf-8")
text = text.replace(
    "from pilot_control import SCENARIOS, new_state, record_scenario, validate_state  # noqa: E402\n",
    "from pilot_control import SCENARIOS, new_state as _new_state, record_scenario, validate_state  # noqa: E402\n"
    "from pilot_control_audit import build_audit_entry, normalize_mutation  # noqa: E402\n",
    1,
)
insert_anchor = "}\n\n\ndef valid_changes"
insert = '''}
APPROVALS = {
    "business_owner": "Business",
    "operations_owner": "Operations",
    "technical_owner": "Technical",
    "legal_owner": "Legal",
    "support_owner": "Support",
}


def new_state(binding):
    mutation = normalize_mutation(
        operation="init",
        operator_role="operations_owner",
        operator_name="Operations",
        reason="Initialize controlled pilot state",
        approvals=APPROVALS,
    )
    return _new_state(
        binding,
        initial_audit=build_audit_entry(
            mutation, revision=1, parent_state_sha256=None
        ),
    )


def valid_changes'''
if text.count(insert_anchor) != 1:
    raise SystemExit("control test admission insertion anchor changed unexpectedly")
control_tests.write_text(text.replace(insert_anchor, insert, 1), encoding="utf-8")

# Binding tests use a structurally valid init audit.
binding_tests = Path("backend/tests/test_pilot_control_binding.py")
text = binding_tests.read_text(encoding="utf-8")
text = text.replace(
    "from pilot_control import load_state, new_state  # noqa: E402\n",
    "from pilot_control import load_state, new_state as _new_state  # noqa: E402\n"
    "from pilot_control_audit import build_audit_entry, normalize_mutation  # noqa: E402\n",
    1,
)
helper_anchor = "\n\ndef _manifest(path: Path) -> dict:\n"
helper = '''

APPROVALS = {
    "business_owner": "Business",
    "operations_owner": "Operations",
    "technical_owner": "Technical",
    "legal_owner": "Legal",
    "support_owner": "Support",
}


def new_state(binding):
    mutation = normalize_mutation(
        operation="init",
        operator_role="operations_owner",
        operator_name="Operations",
        reason="Initialize controlled pilot state",
        approvals=APPROVALS,
    )
    return _new_state(
        binding,
        initial_audit=build_audit_entry(
            mutation, revision=1, parent_state_sha256=None
        ),
    )
'''
if text.count(helper_anchor) != 1:
    raise SystemExit("binding test helper anchor changed unexpectedly")
binding_tests.write_text(text.replace(helper_anchor, helper + helper_anchor, 1), encoding="utf-8")

# Signature tests wrap state creation and infer truthful scenario mutation metadata.
signature_tests = Path("backend/tests/test_pilot_control_signature.py")
text = signature_tests.read_text(encoding="utf-8")
text = text.replace(
    "from pilot_control import load_state, new_state, save_state, validate_state  # noqa: E402\n",
    "from pilot_control import load_state, new_state as _new_state, save_state as _save_state, validate_state  # noqa: E402\n"
    "from pilot_control_audit import build_audit_entry, normalize_mutation  # noqa: E402\n",
    1,
)
insert_anchor = "}\n\n\ndef _concurrent_writer"
insert = '''}
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


def _concurrent_writer'''
if text.count(insert_anchor) != 1:
    raise SystemExit("signature test insertion anchor changed unexpectedly")
text = text.replace(insert_anchor, insert, 1)
text = text.replace('assert state["schema_version"] == 4', 'assert state["schema_version"] == 5', 1)
text = text.replace(
    '    path.write_text(json.dumps({"schema_version": 3}), encoding="utf-8")\n'
    '    with pytest.raises(ValueError, match="Replay-vulnerable pilot state schema 3 cannot be reused"):\n'
    '        load_state(path, secret=SECRET)\n',
    '    path.write_text(json.dumps({"schema_version": 3}), encoding="utf-8")\n'
    '    with pytest.raises(ValueError, match="Replay-vulnerable pilot state schema 3 cannot be reused"):\n'
    '        load_state(path, secret=SECRET)\n\n'
    '    path.write_text(json.dumps({"schema_version": 4}), encoding="utf-8")\n'
    '    with pytest.raises(ValueError, match="Unattributed pilot state schema 4 cannot be reused"):\n'
    '        load_state(path, secret=SECRET)\n',
    1,
)
signature_tests.write_text(text, encoding="utf-8")

# Durability tests use the same accountable wrappers.
durability_tests = Path("backend/tests/test_pilot_control_durability.py")
text = durability_tests.read_text(encoding="utf-8")
text = text.replace(
    "    new_state,\n"
    "    refresh_summary,\n"
    "    save_state,\n",
    "    new_state as _new_state,\n"
    "    refresh_summary as _refresh_summary,\n"
    "    save_state as _save_state,\n",
    1,
)
text = text.replace(
    "from pilot_control_chain import state_anchor  # noqa: E402\n",
    "from pilot_control_chain import state_anchor  # noqa: E402\n"
    "from pilot_control_audit import build_audit_entry, normalize_mutation  # noqa: E402\n",
    1,
)
insert_anchor = "}\n\n\ndef _signed_state"
insert = '''}
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


def _signed_state'''
if text.count(insert_anchor) != 1:
    raise SystemExit("durability test insertion anchor changed unexpectedly")
durability_tests.write_text(text.replace(insert_anchor, insert, 1), encoding="utf-8")

# Runtime fixture uses capability v15, schema v5 and an admission-approved init audit.
runtime_tests = Path("backend/tests/test_pilot_runtime.py")
text = runtime_tests.read_text(encoding="utf-8")
text = text.replace(
    "from scripts.pilot_control_binding import build_admission_binding\n",
    "from scripts.pilot_control_audit import build_audit_entry, normalize_mutation\n"
    "from scripts.pilot_control_binding import build_admission_binding\n",
    1,
)
text = text.replace('            "version": 14,\n', '            "version": 15,\n', 1)
text = text.replace('        "schema_version": 4,\n', '        "schema_version": 5,\n', 1)
text = text.replace(
    '    manifest = {\n'
    '        "kind": "pilot_admission",\n',
    '    approvals = {\n'
    '        "business_owner": "Business",\n'
    '        "operations_owner": "Operations",\n'
    '        "technical_owner": "Technical",\n'
    '        "legal_owner": "Legal",\n'
    '        "support_owner": "Support",\n'
    '    }\n'
    '    manifest = {\n'
    '        "kind": "pilot_admission",\n',
    1,
)
text = text.replace(
    '        "previous_release": previous,\n'
    '        "evidence": {\n',
    '        "previous_release": previous,\n'
    '        "approvals": approvals,\n'
    '        "evidence": {\n',
    1,
)
text = text.replace(
    '    pilot_payload["admission"] = build_admission_binding(manifest_path, signed_manifest)\n'
    '    signed_pilot = sign_payload(pilot_payload, secret)\n',
    '    pilot_payload["admission"] = build_admission_binding(manifest_path, signed_manifest)\n'
    '    init_mutation = normalize_mutation(\n'
    '        operation="init",\n'
    '        operator_role="operations_owner",\n'
    '        operator_name="Operations",\n'
    '        reason="Initialize controlled pilot state",\n'
    '        approvals=approvals,\n'
    '    )\n'
    '    pilot_payload["audit_log"] = [\n'
    '        build_audit_entry(\n'
    '            init_mutation,\n'
    '            revision=1,\n'
    '            parent_state_sha256=None,\n'
    '            changed_at=pilot_created_at,\n'
    '        )\n'
    '    ]\n'
    '    signed_pilot = sign_payload(pilot_payload, secret)\n',
    1,
)
runtime_tests.write_text(text, encoding="utf-8")

runtime_cli_tests = Path("backend/tests/test_pilot_runtime_cli.py")
text = runtime_cli_tests.read_text(encoding="utf-8")
text = text.replace(
    "def test_host_arm_requires_replay_resistant_schema_v4_control_state():",
    "def test_host_arm_requires_accountable_schema_v5_control_state():",
    1,
)
text = text.replace(
    'assert \'pilot_state.get("schema_version") != 4\' in source',
    'assert \'pilot_state.get("schema_version") != 5\' in source',
    1,
)
text += '''


def test_runtime_arm_validates_audit_owners_against_signed_admission():
    source = (ROOT / "scripts/pilot_runtime.py").read_text(encoding="utf-8")
    assert "approved_operators(manifest)" in source
    assert "validate_audit_log(" in source
'''
runtime_cli_tests.write_text(text, encoding="utf-8")

# Capability v15 immutable guards.
capability = Path("scripts/pilot_release_capability.py")
text = capability.read_text(encoding="utf-8")
text = text.replace(
    '    "scripts/pilot_control_binding.py",\n'
    '    "scripts/pilot_control_chain.py",\n',
    '    "scripts/pilot_control_binding.py",\n'
    '    "scripts/pilot_control_audit.py",\n'
    '    "scripts/pilot_control_chain.py",\n',
    1,
)
text = text.replace(
    '    "backend/tests/test_pilot_control_binding.py",\n'
    '    "backend/tests/test_pilot_control_signature.py",\n',
    '    "backend/tests/test_pilot_control_binding.py",\n'
    '    "backend/tests/test_pilot_control_audit.py",\n'
    '    "backend/tests/test_pilot_control_signature.py",\n',
    1,
)
text = text.replace('(\"CAPABILITY_VERSION = 14\",)', '(\"CAPABILITY_VERSION = 15\",)', 1)
text = text.replace(
    '_require_markers(bundle, files, "scripts/pilot_control.py", ("SCHEMA_VERSION = 4", "durable_atomic_write_text(", "def refresh_summary(", "derived and non-authoritative", "return _report_exit(report, final=args.final)"), errors)',
    '_require_markers(bundle, files, "scripts/pilot_control.py", ("SCHEMA_VERSION = 5", "verified_admission_context(", "approved_operator_names=args.approved_operators", "mutation=_mutation_from_args(", "Unattributed pilot state schema 4 cannot be reused", "Last accountable mutation"), errors)',
    1,
)
chain_anchor = '            _require_markers(bundle, files, "scripts/pilot_control_chain.py", ("def signed_state_sha256(", "def validate_anchor_transition(", "pilot control state revision rollback detected", "pilot control state ancestry does not match the armed runtime"), errors)\n'
chain_addition = (
    '            _require_markers(bundle, files, "scripts/pilot_control_audit.py", '
    '("APPROVAL_ROLES", "def approved_operators(", "def normalize_mutation(", '
    '"def validate_audit_log(", "def validate_record_mutation(", '
    '"does not match signed admission owner"), errors)\n'
    + chain_anchor
)
if text.count(chain_anchor) != 1:
    raise SystemExit("capability chain marker changed unexpectedly")
text = text.replace(chain_anchor, chain_addition, 1)
text = text.replace(
    '_require_markers(bundle, files, "backend/services/pilot_runtime.py", ("build_admission_binding(manifest_path, manifest)", "validate_state_descendant(", "validated_anchor.update(state_anchor(pilot_state))", "state.pilot_state_revision", "state.pilot_state_sha256", "armed runtime pilot state replay anchor is missing"), errors)',
    '_require_markers(bundle, files, "backend/services/pilot_runtime.py", ("build_admission_binding(manifest_path, manifest)", "validate_state_descendant(", "validate_audit_log(", "approved_operators(manifest)", "state.pilot_state_revision", "armed runtime pilot state replay anchor is missing"), errors)',
    1,
)
text = text.replace(
    '_require_markers(bundle, files, "scripts/pilot_runtime.py", ("build_admission_binding(DEFAULT_MANIFEST, manifest)", "pilot_state_revision", "pilot_state_sha256", "pilot_state_history", "validate_anchor_transition(", "Stopped pilot runtime cannot change admission or release lineage"), errors)',
    '_require_markers(bundle, files, "scripts/pilot_runtime.py", ("build_admission_binding(DEFAULT_MANIFEST, manifest)", "validate_audit_log(", "approved_operators(manifest)", "pilot_state_revision", "validate_anchor_transition(", "Stopped pilot runtime cannot change admission or release lineage"), errors)',
    1,
)
text = text.replace(
    '_require_markers(bundle, files, "Makefile", ("python3 scripts/pilot_runner.py init", "python3 scripts/pilot_runner.py record $(ARGS)", "python3 scripts/pilot_runner.py status", "python3 scripts/pilot_runner.py validate --final"), errors)',
    '_require_markers(bundle, files, "Makefile", ("python3 scripts/pilot_runner.py init $(ARGS)", "--operator-role operations_owner", "python3 scripts/pilot_runner.py record $(ARGS)", "python3 scripts/pilot_runner.py status", "python3 scripts/pilot_runner.py validate --final"), errors)',
    1,
)
binding_test_anchor = '            _require_markers(bundle, files, "backend/tests/test_pilot_control_binding.py", ("test_state_is_bound_to_one_exact_signed_admission_file", "test_legacy_state_is_rejected_without_silent_migration", "test_makefile_routes_pilot_control_through_admission_runner"), errors)\n'
binding_test_addition = binding_test_anchor + (
    '            _require_markers(bundle, files, "backend/tests/test_pilot_control_audit.py", '
    '("test_init_and_record_are_bound_to_admission_owners_and_lineage", '
    '"test_unapproved_name_or_role_is_rejected", '
    '"test_misleading_scenario_audit_is_rejected", '
    '"test_tampered_or_unapproved_audit_fails_state_load", '
    '"test_init_and_record_parser_require_accountable_identity"), errors)\n'
)
if text.count(binding_test_anchor) != 1:
    raise SystemExit("capability binding test marker changed unexpectedly")
capability.write_text(text.replace(binding_test_anchor, binding_test_addition, 1), encoding="utf-8")

capability_tests = Path("backend/tests/test_pilot_release_capability.py")
text = capability_tests.read_text(encoding="utf-8")
text = text.replace("CAPABILITY_VERSION = 14", "CAPABILITY_VERSION = 15")
text = text.replace("assert CAPABILITY_VERSION == 14", "assert CAPABILITY_VERSION == 15")
old_control_fixture = '''    "scripts/pilot_control.py": (
        "SCHEMA_VERSION = 4\\n"
        "durable_atomic_write_text(\\n"
        "def refresh_summary(): pass\\n"
        "derived and non-authoritative\\n"
        "return _report_exit(report, final=args.final)\\n"
    ),
'''
new_control_fixture = '''    "scripts/pilot_control.py": (
        "SCHEMA_VERSION = 5\\nverified_admission_context(\\n"
        "approved_operator_names=args.approved_operators\\n"
        "mutation=_mutation_from_args(\\n"
        "Unattributed pilot state schema 4 cannot be reused\\n"
        "Last accountable mutation\\n"
    ),
    "scripts/pilot_control_audit.py": (
        "APPROVAL_ROLES\\ndef approved_operators(): pass\\n"
        "def normalize_mutation(): pass\\ndef validate_audit_log(): pass\\n"
        "def validate_record_mutation(): pass\\n"
        "does not match signed admission owner\\n"
    ),
'''
if text.count(old_control_fixture) != 1:
    raise SystemExit("capability control fixture changed unexpectedly")
text = text.replace(old_control_fixture, new_control_fixture, 1)
text = text.replace(
    '        "validate_state_descendant(\\n"\n'
    '        "validated_anchor.update(state_anchor(pilot_state))\\n"\n'
    '        "state.pilot_state_revision\\nstate.pilot_state_sha256\\n"\n',
    '        "validate_state_descendant(\\n"\n'
    '        "validate_audit_log(\\napproved_operators(manifest)\\n"\n'
    '        "state.pilot_state_revision\\nstate.pilot_state_sha256\\n"\n',
    1,
)
text = text.replace(
    '        "build_admission_binding(DEFAULT_MANIFEST, manifest)\\n"\n'
    '        "pilot_state_revision\\npilot_state_sha256\\npilot_state_history\\n"\n',
    '        "build_admission_binding(DEFAULT_MANIFEST, manifest)\\n"\n'
    '        "validate_audit_log(\\napproved_operators(manifest)\\n"\n'
    '        "pilot_state_revision\\npilot_state_sha256\\npilot_state_history\\n"\n',
    1,
)
text = text.replace(
    '        "python3 scripts/pilot_runner.py init\\n"\n',
    '        "python3 scripts/pilot_runner.py init $(ARGS)\\n"\n'
    '        "--operator-role operations_owner\\n"\n',
    1,
)
binding_fixture = '''    "backend/tests/test_pilot_control_binding.py": (
        "test_state_is_bound_to_one_exact_signed_admission_file\\n"
        "test_legacy_state_is_rejected_without_silent_migration\\n"
        "test_makefile_routes_pilot_control_through_admission_runner\\n"
    ),
'''
audit_fixture = binding_fixture + '''    "backend/tests/test_pilot_control_audit.py": (
        "test_init_and_record_are_bound_to_admission_owners_and_lineage\\n"
        "test_unapproved_name_or_role_is_rejected\\n"
        "test_misleading_scenario_audit_is_rejected\\n"
        "test_tampered_or_unapproved_audit_fails_state_load\\n"
        "test_init_and_record_parser_require_accountable_identity\\n"
    ),
'''
if text.count(binding_fixture) != 1:
    raise SystemExit("capability binding fixture changed unexpectedly")
capability_tests.write_text(text.replace(binding_fixture, audit_fixture, 1), encoding="utf-8")

repository_test = Path("backend/tests/test_pilot_release_capability_repository.py")
text = repository_test.read_text(encoding="utf-8").replace("v14", "v15")
repository_test.write_text(text, encoding="utf-8")

# Documentation and E2E matrix.
runbook = Path("docs/pilot/admission_bound_state_migration.md")
text = runbook.read_text(encoding="utf-8")
text = text.replace("state schema v4", "state schema v5")
text = text.replace("fresh schema v4 state", "fresh schema v5 state")
text = text.replace("fresh replay-resistant schema v4 state", "fresh accountable schema v5 state")
text = text.replace("capability v14", "capability v15")
text = text.replace(
    "Schema v1 is admission-unbound. Schema v2 is admission-bound but unsigned. Schema v3 is signed but has no database-anchored replay lineage. All three are intentionally rejected and are never migrated in place.",
    "Schema v1 is admission-unbound. Schema v2 is admission-bound but unsigned. Schema v3 is signed but has no database-anchored replay lineage. Schema v4 has replay protection but no admission-owner audit. All four are intentionally rejected and are never migrated in place.",
)
text = text.replace(
    "make pilot-init\nmake pilot-record ARGS='--number 1 --result running --evidence <reference>'",
    "make pilot-init ARGS='--operator-role operations_owner --operator \"<signed admission owner>\" --reason \"Initialize controlled pilot\"'\n"
    "make pilot-record ARGS='--number 1 --result running --operator-role operations_owner --operator \"<signed admission owner>\" --reason \"Verified scenario 1\" --evidence <reference>'",
)
text = text.replace(
    "Every target revalidates the signed admission and verifies the current state signature.",
    "Every target revalidates the signed admission and verifies the current state signature. Every init or record mutation must name an operator role and exact operator name matching that role in the signed admission, plus a durable reason. The signed audit log records a UUID, revision, parent state hash, timestamp, role, owner name, scenario and result; misleading scenario metadata or protected top-level changes are rejected.",
)
text = text.replace(
    "- legacy schema v1, unsigned schema v2 or replay-vulnerable schema v3 state;\n",
    "- legacy schema v1, unsigned schema v2, replay-vulnerable schema v3 or unattributed schema v4 state;\n"
    "- mutation operator not matching the signed admission owner;\n"
    "- mutation reason, scenario or result not matching the actual state change;\n",
)
runbook.write_text(text, encoding="utf-8")

matrix = Path("docs/pilot/end_to_end_coverage_matrix.md")
text = matrix.read_text(encoding="utf-8")
text = text.replace(
    "| Runtime pilot guard | Allowlist -> signed live gate -> signed admission -> crash-durable locked replay-resistant lineage -> first 20 orders -> automatic STOP | Capability v14, fsync/summary-repair/lock/lineage/DB-anchor/admission/runtime tests | PASS | Requires fresh signed provider/live evidence and controlled live run |",
    "| Runtime pilot guard | Allowlist -> signed live gate -> signed admission -> accountable crash-durable locked lineage -> first 20 orders -> automatic STOP | Capability v15, admission-owner audit/fsync/lock/lineage/DB-anchor/runtime tests | PASS | Requires fresh signed provider/live evidence and controlled live run |",
)
text = text.replace(
    "The JSON is authoritative; a stale or missing derived Markdown summary is repaired from the signed state without creating a new revision.",
    "The JSON is authoritative; a stale or missing derived Markdown summary is repaired from the signed state without creating a new revision. Every mutation is append-only audited to an exact named admission owner and reason; arbitrary operator names and misleading scenario metadata fail closed.",
)
matrix.write_text(text, encoding="utf-8")
