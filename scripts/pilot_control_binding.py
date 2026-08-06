"""Bind a controlled pilot state to one exact signed admission manifest."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Mapping

RELEASE_KEYS = ("release_id", "git_commit", "sha256")
ROOT = Path(__file__).resolve().parents[1]
LIVE_LIFECYCLE_KEY = "live_lifecycle_report_sha256"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _requires_live_lifecycle(manifest: Mapping[str, Any]) -> bool:
    """Only a baseline-verified production GO manifest enters this path."""
    return (
        manifest.get("kind") == "pilot_admission"
        and manifest.get("decision") == "GO"
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
) -> str:
    from pilot_lifecycle_admission import validate_attached_lifecycle

    errors = validate_attached_lifecycle(
        manifest_path,
        manifest,
        env=_runtime_evidence_env(root),
        root=root,
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


def build_admission_binding(
    manifest_path: Path,
    manifest: Mapping[str, Any],
    *,
    root: Path | None = None,
    require_live_lifecycle: bool | None = None,
) -> dict[str, Any]:
    """Create the immutable identity that a single pilot state must retain."""
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
    binding: dict[str, Any] = {
        "manifest_sha256": sha256_file(manifest_path),
        "created_at": created_at,
        "configuration_fingerprint": fingerprint,
        "release": normalized_release,
    }
    if enforce_lifecycle:
        binding[LIVE_LIFECYCLE_KEY] = _live_lifecycle_sha256(
            manifest_path,
            manifest,
            root=(root or ROOT),
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
