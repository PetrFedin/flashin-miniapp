"""Bind a controlled pilot state to one exact signed admission manifest."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping

RELEASE_KEYS = ("release_id", "git_commit", "sha256")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_admission_binding(
    manifest_path: Path,
    manifest: Mapping[str, Any],
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
    return {
        "manifest_sha256": sha256_file(manifest_path),
        "created_at": created_at,
        "configuration_fingerprint": fingerprint,
        "release": normalized_release,
    }


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
