from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, TYPE_CHECKING

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from scripts.pilot_release_contract import CAPABILITY_VERSION
from scripts.pilot_control_audit import approved_operators, validate_audit_log
from scripts.pilot_control_binding import build_admission_binding, validate_admission_binding
from scripts.pilot_control_chain import (
    state_anchor,
    validate_state_chain,
    validate_state_descendant,
)
from scripts.pilot_evidence import (
    configuration_fingerprint,
    require_signing_secret,
    verify_payload_signature,
)

from ..database import utcnow_naive
from .pilot_database_evidence import validate_pilot_database_evidence
from ..pilot_models import PilotOrderSlot, PilotRuntimeState

if TYPE_CHECKING:
    from ..config import Settings
    from ..models import Customer, Order


@dataclass(frozen=True)
class PilotCheckoutContext:
    run_id: str
    sequence: int
    admission_sha256: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path, label: str) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, [f"{label} is missing"]
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"{label} is invalid: {exc}"]
    if not isinstance(payload, dict):
        return None, [f"{label} must contain a JSON object"]
    return payload, []


def _parse_allowlist(raw: str) -> tuple[set[str], list[str]]:
    try:
        values = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return set(), ["pilot allowlist is invalid JSON"]
    if not isinstance(values, list):
        return set(), ["pilot allowlist must be a list"]
    normalized = [str(value).strip() for value in values]
    if any(not value for value in normalized):
        return set(), ["pilot allowlist contains an empty identifier"]
    if len(normalized) != len(set(normalized)):
        return set(), ["pilot allowlist contains duplicates"]
    return set(normalized), []


def _resolve_evidence_path(manifest_path: Path, raw_path: object) -> Path:
    name = Path(str(raw_path or "")).name
    if not name:
        return manifest_path.parent / "__missing__"
    pilot_candidate = manifest_path.parent / name
    if pilot_candidate.exists():
        return pilot_candidate
    return manifest_path.parent.parent / name


def _validate_release_capability(
    release_state: Mapping[str, Any],
    secret: str,
    label: str,
) -> list[str]:
    capabilities = release_state.get("capabilities")
    capability = capabilities.get("pilot_runtime_guard") if isinstance(capabilities, Mapping) else None
    if not isinstance(capability, Mapping):
        return [f"{label} release is missing signed pilot_runtime_guard capability"]
    errors: list[str] = []
    if not verify_payload_signature(capability, secret):
        errors.append(f"{label} release runtime capability signature is invalid")
    expected = {
        "schema_version": 1,
        "kind": "release_capability",
        "name": "pilot_runtime_guard",
        "version": CAPABILITY_VERSION,
        "archive_sha256": release_state.get("sha256"),
        "git_commit": release_state.get("git_commit"),
        "release_id": release_state.get("release_id"),
    }
    for key, value in expected.items():
        if capability.get(key) != value:
            errors.append(f"{label} release runtime capability {key} mismatch")
    return errors


def validate_runtime_files(
    state: PilotRuntimeState,
    settings: "Settings",
    *,
    env: Mapping[str, str] | None = None,
    validated_anchor: dict[str, Any] | None = None,
    validated_pilot_state: dict[str, Any] | None = None,
) -> list[str]:
    errors: list[str] = []
    runtime_env: Mapping[str, str] = env or os.environ
    manifest_path = Path(settings.pilot_admission_manifest_path)
    current_path = Path(settings.pilot_current_release_path)
    previous_path = Path(settings.pilot_previous_release_path)
    pilot_state_path = Path(settings.pilot_state_path)

    manifest, manifest_errors = _load_json(manifest_path, "pilot admission manifest")
    current_release, current_errors = _load_json(current_path, "current release pointer")
    previous_release, previous_errors = _load_json(previous_path, "previous release pointer")
    pilot_state, pilot_errors = _load_json(pilot_state_path, "pilot control state")
    errors.extend(manifest_errors + current_errors + previous_errors + pilot_errors)
    if errors:
        return list(dict.fromkeys(errors))

    assert manifest is not None
    assert current_release is not None
    assert previous_release is not None
    assert pilot_state is not None

    try:
        secret = require_signing_secret(
            {
                **runtime_env,
                "PILOT_EVIDENCE_SIGNING_SECRET": settings.pilot_evidence_signing_secret,
            }
        )
    except ValueError as exc:
        return [str(exc)]

    if manifest.get("kind") != "pilot_admission" or manifest.get("decision") != "GO":
        errors.append("pilot admission manifest is not GO")
    if not verify_payload_signature(manifest, secret):
        errors.append("pilot admission signature is invalid")
    expected_fingerprint = configuration_fingerprint(runtime_env, secret)
    if manifest.get("configuration_fingerprint") != expected_fingerprint:
        errors.append("pilot admission configuration fingerprint does not match")
    if sha256_file(manifest_path) != state.admission_sha256:
        errors.append("pilot admission file does not match the armed runtime")

    current_binding = manifest.get("release")
    previous_binding = manifest.get("previous_release")
    if not isinstance(current_binding, Mapping):
        errors.append("pilot admission current release binding is missing")
    elif str(current_binding.get("sha256", "")) != state.release_sha256:
        errors.append("pilot admission current release does not match the armed runtime")
    if not isinstance(previous_binding, Mapping):
        errors.append("pilot admission previous release binding is missing")
    elif str(previous_binding.get("sha256", "")) != str(previous_release.get("sha256", "")):
        errors.append("pilot admission previous release does not match the rollback pointer")
    if str(current_release.get("sha256", "")) != state.release_sha256:
        errors.append("current release pointer does not match the armed runtime")
    if str(current_release.get("sha256", "")) == str(previous_release.get("sha256", "")):
        errors.append("current and previous release pointers must be different")
    errors.extend(_validate_release_capability(current_release, secret, "current"))
    errors.extend(_validate_release_capability(previous_release, secret, "previous"))

    evidence = manifest.get("evidence")
    if not isinstance(evidence, Mapping):
        errors.append("pilot admission evidence map is missing")
    else:
        for key in ("provider_report", "live_gate_report", "rollback_drill_report"):
            entry = evidence.get(key)
            if not isinstance(entry, Mapping):
                errors.append(f"pilot admission evidence is missing: {key}")
                continue
            path = _resolve_evidence_path(manifest_path, entry.get("path"))
            if not path.is_file():
                errors.append(f"pilot evidence file is missing: {key}")
                continue
            if str(entry.get("sha256", "")) != sha256_file(path):
                errors.append(f"pilot evidence checksum does not match: {key}")

    if pilot_state.get("schema_version") != 6:
        errors.append("pilot control state schema is unsupported")
    elif pilot_state.get("database_evidence_contract") != 1:
        errors.append("pilot database evidence contract is missing or unsupported")
    elif not verify_payload_signature(pilot_state, secret):
        errors.append("pilot control state signature is invalid")
    else:
        chain_errors = validate_state_chain(pilot_state)
        errors.extend(chain_errors)
        try:
            errors.extend(
                validate_audit_log(
                    pilot_state, approvals=approved_operators(manifest)
                )
            )
        except ValueError as exc:
            errors.append(str(exc))
        try:
            expected_binding = build_admission_binding(manifest_path, manifest)
            errors.extend(validate_admission_binding(pilot_state, expected_binding))
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
        if state.status in {"active", "stopped"} and (
            state.pilot_state_revision < 1 or len(state.pilot_state_sha256) != 64
        ):
            errors.append("armed runtime pilot state replay anchor is missing")
        elif not chain_errors:
            errors.extend(
                validate_state_descendant(
                    pilot_state,
                    anchored_revision=state.pilot_state_revision,
                    anchored_sha256=state.pilot_state_sha256,
                )
            )
    if pilot_state.get("created_at") != state.pilot_state_created_at:
        errors.append("pilot control state was replaced after runtime arm")
    if pilot_state.get("decision") == "STOP":
        errors.append("pilot control decision is STOP")
    scenarios = pilot_state.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) != 20:
        errors.append("pilot control state must contain exactly 20 scenarios")

    unique_errors = list(dict.fromkeys(errors))
    if not unique_errors and validated_anchor is not None:
        validated_anchor.update(state_anchor(pilot_state))
    if not unique_errors and validated_pilot_state is not None:
        validated_pilot_state.clear()
        validated_pilot_state.update(json.loads(json.dumps(pilot_state)))
    return unique_errors


def _blocked() -> HTTPException:
    return HTTPException(
        status_code=423,
        detail={
            "code": "pilot_checkout_unavailable",
            "message": "Checkout is temporarily unavailable during the controlled pilot.",
        },
    )


def _integrity_failure() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "pilot_runtime_integrity_failure",
            "message": "Checkout is unavailable because pilot runtime integrity could not be verified.",
        },
    )


def acquire_pilot_checkout(
    db: Session,
    *,
    customer: "Customer",
    settings: "Settings",
    env: Mapping[str, str] | None = None,
) -> PilotCheckoutContext | None:
    if not settings.pilot_runtime_enforced:
        return None

    state = (
        db.query(PilotRuntimeState)
        .filter(PilotRuntimeState.id == 1)
        .with_for_update()
        .first()
    )
    if not state or state.status != "active":
        raise _blocked()
    if state.max_orders != settings.pilot_runtime_max_orders or state.max_orders != 20:
        raise _integrity_failure()

    current_anchor: dict[str, Any] = {}
    current_pilot_state: dict[str, Any] = {}
    file_errors = validate_runtime_files(
        state,
        settings,
        env=env,
        validated_anchor=current_anchor,
        validated_pilot_state=current_pilot_state,
    )
    if file_errors:
        if "pilot control decision is STOP" in file_errors:
            raise _blocked()
        raise _integrity_failure()
    database_errors = validate_pilot_database_evidence(
        db,
        current_pilot_state,
        state,
        final=False,
    )
    if database_errors:
        raise _integrity_failure()

    state.pilot_state_revision = int(current_anchor["revision"])
    state.pilot_state_sha256 = str(current_anchor["sha256"])
    state.updated_at = utcnow_naive()

    allowlist, allowlist_errors = _parse_allowlist(state.allowed_telegram_ids)
    if allowlist_errors:
        raise _integrity_failure()
    if str(customer.telegram_id).strip() not in allowlist:
        raise _blocked()

    slot_count = (
        db.query(func.count(PilotOrderSlot.id))
        .filter(PilotOrderSlot.run_id == state.run_id)
        .scalar()
        or 0
    )
    if slot_count != state.accepted_orders:
        raise _integrity_failure()
    if state.accepted_orders >= state.max_orders:
        raise _blocked()

    return PilotCheckoutContext(
        run_id=state.run_id,
        sequence=state.accepted_orders + 1,
        admission_sha256=state.admission_sha256,
    )


def record_pilot_order(
    db: Session,
    *,
    context: PilotCheckoutContext | None,
    order: "Order",
    customer: "Customer",
) -> None:
    if context is None:
        return

    state = (
        db.query(PilotRuntimeState)
        .filter(PilotRuntimeState.id == 1)
        .with_for_update()
        .first()
    )
    if not state or state.status != "active" or state.run_id != context.run_id:
        raise _integrity_failure()
    if state.admission_sha256 != context.admission_sha256:
        raise _integrity_failure()
    if state.accepted_orders + 1 != context.sequence or context.sequence > state.max_orders:
        raise _integrity_failure()

    db.add(
        PilotOrderSlot(
            run_id=context.run_id,
            sequence=context.sequence,
            order_id=order.id,
            customer_id=customer.id,
            admission_sha256=context.admission_sha256,
        )
    )
    state.accepted_orders = context.sequence
    state.updated_at = utcnow_naive()
    if state.accepted_orders == state.max_orders:
        state.status = "completed"
        state.completed_at = utcnow_naive()
