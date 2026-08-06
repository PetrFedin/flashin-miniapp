#!/usr/bin/env python3
"""Verify that the exact promoted release contains the v18 lifecycle gate.

The shared runtime capability v17 remains the rollback/runtime baseline. This
additional release guard prevents an older v17 archive from being admitted as
a v18 live-lifecycle pilot merely because it still satisfies the older base
capability contract.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, Mapping

from release_control import MANIFEST_NAME, verify_release

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FILES: dict[str, tuple[str, ...]] = {
    "scripts/pilot_live_lifecycle.py": (
        '"kind": "pilot_live_lifecycle"',
        "BASE_REQUIRED_SCENARIOS",
        "validate_live_lifecycle_report(",
        "docs/pilot/evidence",
    ),
    "scripts/pilot_lifecycle_admission.py": (
        "attach_lifecycle_report(",
        "validate_attached_lifecycle(",
        "live_lifecycle_completed",
        "require_current_lifecycle_release(",
    ),
    "scripts/pilot_lifecycle_release_guard.py": (
        "REQUIRED_FILES",
        "inspect_lifecycle_release(",
        "require_current_lifecycle_release(",
        "Current release is not lifecycle-capable",
    ),
    "scripts/pilot_control_binding.py": (
        "LIVE_LIFECYCLE_KEY",
        "_requires_live_lifecycle(",
        "live lifecycle evidence does not match",
    ),
    "backend/tests/test_pilot_live_lifecycle.py": (
        "test_live_lifecycle_report_requires_exact_deployed_scenarios_and_hashes",
        "test_go_admission_binding_requires_attached_live_lifecycle",
        "test_evidence_symlink_and_outside_repository_are_rejected",
    ),
    "backend/tests/test_pilot_lifecycle_release_guard.py": (
        "test_old_runtime_capability_archive_cannot_receive_lifecycle_admission",
        "test_exact_release_requires_every_lifecycle_file_and_marker",
        "test_current_release_pointer_must_match_lifecycle_archive",
    ),
    "docs/pilot/live_lifecycle_evidence.md": (
        "telegram_real_auth",
        "yookassa_duplicate_webhook",
        "pilot-lifecycle-attach",
    ),
    "Makefile": (
        "pilot-lifecycle-create:",
        "pilot-lifecycle-attach:",
        "pilot-lifecycle-status:",
    ),
    ".env.production.example": (
        "PILOT_LIFECYCLE_EVIDENCE_MAX_AGE_HOURS=",
    ),
}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Release state not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Release state is invalid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Release state must contain a JSON object: {path}")
    return payload


def inspect_lifecycle_release(archive: Path) -> list[str]:
    verification = verify_release(archive)
    errors = [str(item) for item in verification.get("errors", [])]
    if not verification.get("ok"):
        return errors or ["Release archive verification failed"]
    try:
        with zipfile.ZipFile(archive, "r") as bundle:
            manifest = json.loads(bundle.read(MANIFEST_NAME))
            files = manifest.get("files")
            if not isinstance(files, Mapping):
                return ["Release manifest file map is invalid"]
            for path, markers in REQUIRED_FILES.items():
                if path not in files:
                    errors.append(f"Lifecycle-capable release is missing file: {path}")
                    continue
                content = bundle.read(path).decode("utf-8")
                for marker in markers:
                    if marker not in content:
                        errors.append(
                            f"Lifecycle-capable release marker is missing in {path}: {marker}"
                        )
    except (
        OSError,
        KeyError,
        UnicodeDecodeError,
        zipfile.BadZipFile,
        json.JSONDecodeError,
    ) as exc:
        errors.append(f"Unable to inspect live lifecycle release capability: {exc}")
    return list(dict.fromkeys(errors))


def require_current_lifecycle_release(root: Path = ROOT) -> dict[str, Any]:
    state_path = root / "deploy/release/runtime/current_release.json"
    state = _load_json(state_path)
    archive_raw = str(state.get("archive", "")).strip()
    if not archive_raw:
        raise ValueError("Current release archive path is missing")
    archive = Path(archive_raw)
    if not archive.is_absolute():
        archive = root / archive
    errors = inspect_lifecycle_release(archive)
    if errors:
        raise ValueError(
            "Current release is not lifecycle-capable: " + "; ".join(errors)
        )
    verification = verify_release(archive)
    for key in ("release_id", "git_commit", "sha256"):
        if str(state.get(key, "")) != str(verification.get(key, "")):
            raise ValueError(f"Current release pointer {key} does not match archive")
    return state
