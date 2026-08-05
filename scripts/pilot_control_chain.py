"""Replay-resistant lineage helpers for signed pilot control states."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping, Sequence

try:
    from .pilot_control_audit import validate_audit_log
except ImportError:  # script execution mode
    from pilot_control_audit import validate_audit_log

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def signed_state_sha256(state: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(state)).hexdigest()


def validate_chain_fields(revision: object, history: object) -> list[str]:
    errors: list[str] = []
    if type(revision) is not int or revision < 1:
        errors.append("pilot control state revision must be a positive integer")
        return errors
    if not isinstance(history, list):
        errors.append("pilot control state history must be a list")
        return errors
    if len(history) != revision - 1:
        errors.append("pilot control state history length does not match revision")
    for index, value in enumerate(history, start=1):
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            errors.append(f"pilot control state history hash #{index} is invalid")
    if len(history) != len(set(history)):
        errors.append("pilot control state history contains duplicate hashes")
    return list(dict.fromkeys(errors))


def validate_state_chain(state: Mapping[str, Any]) -> list[str]:
    errors = validate_chain_fields(
        state.get("revision"), state.get("state_history_sha256")
    )
    if not errors:
        errors.extend(validate_audit_log(state))
    return list(dict.fromkeys(errors))


def require_state_chain(state: Mapping[str, Any]) -> None:
    errors = validate_state_chain(state)
    if errors:
        raise ValueError("; ".join(errors))


def state_anchor(state: Mapping[str, Any]) -> dict[str, Any]:
    require_state_chain(state)
    return {
        "revision": int(state["revision"]),
        "sha256": signed_state_sha256(state),
        "history": list(state["state_history_sha256"]),
    }


def validate_anchor_transition(
    *,
    revision: object,
    sha256: object,
    history: object,
    anchored_revision: object,
    anchored_sha256: object,
) -> list[str]:
    errors = validate_chain_fields(revision, history)
    current_sha = str(sha256 or "")
    if not _SHA256.fullmatch(current_sha):
        errors.append("pilot control state SHA-256 is invalid")
    if type(anchored_revision) is not int or anchored_revision < 0:
        errors.append("armed pilot state revision is invalid")
        return list(dict.fromkeys(errors))
    anchor_sha = str(anchored_sha256 or "")
    if anchored_revision == 0:
        if anchor_sha:
            errors.append("uninitialized pilot state anchor contains a SHA-256")
        return list(dict.fromkeys(errors))
    if not _SHA256.fullmatch(anchor_sha):
        errors.append("armed pilot state SHA-256 is invalid")
        return list(dict.fromkeys(errors))
    if errors:
        return list(dict.fromkeys(errors))
    current_revision = int(revision)
    current_history = list(history) if isinstance(history, list) else []
    if current_revision < anchored_revision:
        errors.append("pilot control state revision rollback detected")
    elif current_revision == anchored_revision:
        if current_sha != anchor_sha:
            errors.append("pilot control state hash does not match the armed runtime")
    else:
        index = anchored_revision - 1
        if index >= len(current_history) or current_history[index] != anchor_sha:
            errors.append("pilot control state ancestry does not match the armed runtime")
    return list(dict.fromkeys(errors))


def validate_state_descendant(
    state: Mapping[str, Any],
    *,
    anchored_revision: int,
    anchored_sha256: str,
) -> list[str]:
    return validate_anchor_transition(
        revision=state.get("revision"),
        sha256=signed_state_sha256(state),
        history=state.get("state_history_sha256"),
        anchored_revision=anchored_revision,
        anchored_sha256=anchored_sha256,
    )
