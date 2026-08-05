"""Accountable append-only audit entries for signed pilot control states."""

from __future__ import annotations

from datetime import UTC, datetime
import re
from typing import Any, Mapping
import uuid

try:
    from .script_time import utc_timestamp
except ImportError:  # script execution mode
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
    if any(ord(character) < 32 and character not in "	" for character in reason):
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
