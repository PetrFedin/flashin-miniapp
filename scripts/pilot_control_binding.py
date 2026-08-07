"""Bind a controlled pilot state to one exact signed admission manifest."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Mapping

RELEASE_KEYS = ("release_id", "git_commit", "sha256")
ROOT = Path(__file__).resolve().parents[1]
LIVE_LIFECYCLE_KEY = "live_lifecycle_report_sha256"
REPOSITORY_GOVERNANCE_KEY = "repository_governance_report_sha256"
LAUNCH_CHECKLIST_KEY = "launch_checklist_report_sha256"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _requires_live_lifecycle(manifest: Mapping[str, Any]) -> bool:
    """Require v18 only for the authoritative admission schema.

    Historical unit fixtures without ``schema_version`` are intentionally not
    treated as production admission manifests. The real admission builder and
    verifier require schema version 1, and every schema-v1 GO must carry the
    signed live lifecycle attachment.
    """
    return (
        manifest.get("schema_version") == 1
        and manifest.get("kind") == "pilot_admission"
        and manifest.get("decision") == "GO"
    )


def _requires_repository_governance(manifest: Mapping[str, Any]) -> bool:
    acknowledgements = manifest.get("acknowledgements")
    evidence = manifest.get("evidence")
    pilot_contract = manifest.get("pilot_contract")
    return (
        _requires_live_lifecycle(manifest)
        and isinstance(pilot_contract, Mapping)
        and pilot_contract.get("maximum_orders") == 20
        and pilot_contract.get("mass_admission_forbidden") is True
        and isinstance(acknowledgements, Mapping)
        and acknowledgements.get("live_lifecycle_completed") is True
        and isinstance(evidence, Mapping)
        and isinstance(evidence.get("live_lifecycle_report"), Mapping)
    )


def _requires_launch_checklist(manifest: Mapping[str, Any]) -> bool:
    """Require v21 once a real signed admission has repository governance attached."""
    acknowledgements = manifest.get("acknowledgements")
    evidence = manifest.get("evidence")
    signature = manifest.get("signature")
    return (
        _requires_repository_governance(manifest)
        and isinstance(signature, Mapping)
        and isinstance(acknowledgements, Mapping)
        and acknowledgements.get("repository_governance_verified") is True
        and isinstance(evidence, Mapping)
        and isinstance(evidence.get("repository_governance_report"), Mapping)
    )


def _runtime_evidence_env(root: Path) -> dict[str, str]:
    from pilot_readiness import read_env

    file_env = read_env(root / ".env")
    if file_env:
        return file_env
    return {str(key): str(value) for key, value in os.environ.items()}


def _live_lifecycle_sha256(
    manifest_path: Path,
    manifest: Mapping[str, Any],
    *,
    root: Path,
    now=None,
) -> str:
    from pilot_lifecycle_admission import validate_attached_lifecycle

    errors = validate_attached_lifecycle(
        manifest_path,
        manifest,
        env=_runtime_evidence_env(root),
        root=root,
        now=now,
    )
    if errors:
        raise ValueError(
            "Pilot admission live lifecycle evidence is invalid: "
            + "; ".join(errors)
        )
    evidence = manifest.get("evidence")
    entry = (
        evidence.get("live_lifecycle_report")
        if isinstance(evidence, Mapping)
        else None
    )
    digest = str(entry.get("sha256", "")).strip() if isinstance(entry, Mapping) else ""
    if len(digest) != 64:
        raise ValueError("Pilot admission live lifecycle evidence SHA-256 is invalid")
    return digest


def _repository_governance_sha256(
    manifest_path: Path,
    manifest: Mapping[str, Any],
    *,
    root: Path,
    now=None,
) -> str:
    from pilot_governance_admission import validate_attached_governance
    from pilot_governance_release_guard import require_current_governance_release
    from pilot_operator_security import require_application_token_isolation

    require_application_token_isolation(root, os.environ)
    require_current_governance_release(root)
    errors = validate_attached_governance(
        manifest_path,
        manifest,
        env=_runtime_evidence_env(root),
        root=root,
        now=now,
    )
    if errors:
        raise ValueError(
            "Pilot admission repository governance evidence is invalid: "
            + "; ".join(errors)
        )
    evidence = manifest.get("evidence")
    entry = (
        evidence.get("repository_governance_report")
        if isinstance(evidence, Mapping)
        else None
    )
    digest = str(entry.get("sha256", "")).strip() if isinstance(entry, Mapping) else ""
    if len(digest) != 64:
        raise ValueError("Pilot admission repository governance evidence SHA-256 is invalid")
    return digest


def _launch_checklist_sha256(
    manifest_path: Path,
    manifest: Mapping[str, Any],
    *,
    root: Path,
    now=None,
) -> str:
    from pilot_launch_admission import validate_attached_launch_checklist

    errors = validate_attached_launch_checklist(
        manifest_path,
        manifest,
        env=_runtime_evidence_env(root),
        root=root,
        now=now,
    )
    if errors:
        raise ValueError(
            "Pilot admission launch checklist evidence is invalid: "
            + "; ".join(errors)
        )
    evidence = manifest.get("evidence")
    entry = (
        evidence.get("launch_checklist_report")
        if isinstance(evidence, Mapping)
        else None
    )
    digest = str(entry.get("sha256", "")).strip() if isinstance(entry, Mapping) else ""
    if len(digest) != 64:
        raise ValueError("Pilot admission launch checklist evidence SHA-256 is invalid")
    return digest


def build_admission_binding(
    manifest_path: Path,
    manifest: Mapping[str, Any],
    *,
    root: Path | None = None,
    require_live_lifecycle: bool | None = None,
    require_repository_governance: bool | None = None,
    require_launch_checklist: bool | None = None,
    now=None,
) -> dict[str, Any]:
    """Create the immutable identity that a single pilot state must retain.

    ``now`` exists only to make evidence-age validation deterministic in tests.
    Production callers omit it and therefore validate against the real UTC clock.
    """
    release = manifest.get("release")
    if not isinstance(release, Mapping):
        raise ValueError("Pilot admission release binding is missing")
    created_at = str(manifest.get("created_at", "")).strip()
    fingerprint = str(manifest.get("configuration_fingerprint", "")).strip()
    normalized_release = {key: str(release.get(key, "")).strip() for key in RELEASE_KEYS}
    if not created_at:
        raise ValueError("Pilot admission created_at is missing")
    if len(fingerprint) != 64:
        raise ValueError("Pilot admission configuration fingerprint is invalid")
    if any(not normalized_release[key] for key in RELEASE_KEYS):
        raise ValueError("Pilot admission release binding is incomplete")
    if len(normalized_release["sha256"]) != 64:
        raise ValueError("Pilot admission release SHA-256 is invalid")

    enforce_lifecycle = (
        _requires_live_lifecycle(manifest)
        if require_live_lifecycle is None
        else require_live_lifecycle
    )
    enforce_governance = (
        _requires_repository_governance(manifest)
        if require_repository_governance is None
        else require_repository_governance
    )
    enforce_launch = (
        _requires_launch_checklist(manifest)
        if require_launch_checklist is None
        else require_launch_checklist
    )
    binding: dict[str, Any] = {
        "manifest_sha256": sha256_file(manifest_path),
        "created_at": created_at,
        "configuration_fingerprint": fingerprint,
        "release": normalized_release,
    }
    evidence_root = root or ROOT
    if enforce_lifecycle:
        binding[LIVE_LIFECYCLE_KEY] = _live_lifecycle_sha256(
            manifest_path,
            manifest,
            root=evidence_root,
            now=now,
        )
    if enforce_governance:
        binding[REPOSITORY_GOVERNANCE_KEY] = _repository_governance_sha256(
            manifest_path,
            manifest,
            root=evidence_root,
            now=now,
        )
    if enforce_launch:
        binding[LAUNCH_CHECKLIST_KEY] = _launch_checklist_sha256(
            manifest_path,
            manifest,
            root=evidence_root,
            now=now,
        )
    return binding


def validate_admission_binding(
    state: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> list[str]:
    """Return every mismatch; callers decide how to stop their operation."""
    actual = state.get("admission")
    if not isinstance(actual, Mapping):
        return ["pilot control admission binding is missing"]
    errors: list[str] = []
    for key in ("manifest_sha256", "created_at", "configuration_fingerprint"):
        if actual.get(key) != expected.get(key):
            errors.append(f"pilot control admission {key} does not match current admission")
    expected_lifecycle = expected.get(LIVE_LIFECYCLE_KEY)
    actual_lifecycle = actual.get(LIVE_LIFECYCLE_KEY)
    if expected_lifecycle:
        if actual_lifecycle != expected_lifecycle:
            errors.append("pilot control admission live lifecycle evidence does not match")
    elif actual_lifecycle:
        errors.append("pilot control admission has unexpected live lifecycle evidence")
    expected_governance = expected.get(REPOSITORY_GOVERNANCE_KEY)
    actual_governance = actual.get(REPOSITORY_GOVERNANCE_KEY)
    if expected_governance:
        if actual_governance != expected_governance:
            errors.append("pilot control admission repository governance evidence does not match")
    elif actual_governance:
        errors.append("pilot control admission has unexpected repository governance evidence")
    expected_launch = expected.get(LAUNCH_CHECKLIST_KEY)
    actual_launch = actual.get(LAUNCH_CHECKLIST_KEY)
    if expected_launch:
        if actual_launch != expected_launch:
            errors.append("pilot control admission launch checklist evidence does not match")
    elif actual_launch:
        errors.append("pilot control admission has unexpected launch checklist evidence")
    actual_release = actual.get("release")
    expected_release = expected.get("release")
    if not isinstance(actual_release, Mapping):
        errors.append("pilot control admission release binding is missing")
    elif not isinstance(expected_release, Mapping):
        errors.append("current admission release binding is missing")
    else:
        for key in RELEASE_KEYS:
            if actual_release.get(key) != expected_release.get(key):
                errors.append(f"pilot control admission release {key} does not match")
    return list(dict.fromkeys(errors))


def require_admission_binding(
    state: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> None:
    """Fail closed before a state can be reused under a different admission."""
    errors = validate_admission_binding(state, expected)
    if errors:
        raise ValueError(
            "Pilot control state is not bound to the current signed admission: "
            + "; ".join(errors)
            + ". Archive the old state and initialize a fresh pilot after admission."
        )
