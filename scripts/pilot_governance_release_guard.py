#!/usr/bin/env python3
"""Verify that the exact promoted release contains the v19 governance gate."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, Mapping

from pilot_lifecycle_release_guard import inspect_lifecycle_release
from release_control import MANIFEST_NAME, verify_release

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FILES: dict[str, tuple[str, ...]] = {
    "scripts/pilot_repository_governance.py": (
        '"kind": "pilot_repository_governance"',
        "API_VERSION",
        "collect_snapshot(",
        "_github_token(",
        "required_status_checks",
        "ruleset_bypass_visibility",
        "administrator_bypass_blocked",
        "GitHub ruleset bypass actors are not visible",
        "GitHub CI has no successful completed run for the current release commit",
    ),
    "scripts/pilot_governance_admission.py": (
        "attach_governance_report(",
        "validate_attached_governance(",
        "repository_governance_verified",
        "Live lifecycle evidence",
        "Repository governance evidence",
        "require_current_governance_release(",
    ),
    "scripts/pilot_governance_release_guard.py": (
        "REQUIRED_FILES",
        "inspect_governance_release(",
        "require_current_governance_release(",
        "Current release is not governance-capable",
    ),
    "scripts/pilot_control_binding.py": (
        "REPOSITORY_GOVERNANCE_KEY",
        "_requires_repository_governance(",
        "repository governance evidence does not match",
    ),
    ".github/workflows/ci.yml": (
        "name: CI",
        "  backend:",
        "  frontend:",
        "  admin:",
        "  browser-e2e:",
        "  docker:",
        "Run pilot release, governance and database guard tests",
        "backend/tests/test_pilot_repository_governance.py",
        "backend/tests/test_pilot_governance_release_guard.py",
        "Run Mini App and Admin browser journeys",
        "Run signed backup and restore drill",
        "Run signed full release rollback drill",
        "Validate production Compose isolation",
    ),
    "backend/tests/test_pilot_repository_governance.py": (
        "test_ruleset_governance_report_binds_exact_release_and_successful_ci",
        "test_unprotected_branch_missing_checks_and_bypass_fail_closed",
        "test_governance_attachment_requires_signed_technical_owner",
    ),
    "backend/tests/test_pilot_governance_visibility.py": (
        "test_governance_collection_requires_github_token",
        "test_hidden_ruleset_bypass_data_fails_closed",
        "ruleset_bypass_visibility",
    ),
    "backend/tests/test_pilot_governance_admission_render.py": (
        "test_final_admission_summary_keeps_lifecycle_and_governance_evidence",
        "Live lifecycle evidence",
        "Repository governance evidence",
    ),
    "backend/tests/test_pilot_governance_release_guard.py": (
        "test_lifecycle_only_archive_cannot_receive_governance_admission",
        "test_exact_release_requires_every_governance_file_and_marker",
        "test_current_release_pointer_must_match_governance_archive",
    ),
    "docs/pilot/repository_governance.md": (
        "pilot-governance-create",
        "pilot-governance-attach",
        "backend,frontend,admin,browser-e2e,docker",
        "bypass_actors",
    ),
    "docs/pilot/pilot_launch_runbook.md": (
        "pilot-lifecycle-create",
        "pilot-governance-create",
        "pilot-admission-status",
        "pilot-runtime-arm",
        "pilot-runtime-stop",
        "make pilot-final",
    ),
    "Makefile": (
        "pilot-governance-create:",
        "pilot-governance-attach:",
        "pilot-governance-status:",
    ),
    ".env.production.example": (
        "PILOT_GITHUB_TOKEN=",
        "PILOT_GITHUB_REPOSITORY=",
        "PILOT_GITHUB_GOVERNANCE_MAX_AGE_MINUTES=",
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


def inspect_governance_release(archive: Path) -> list[str]:
    errors = inspect_lifecycle_release(archive)
    verification = verify_release(archive)
    errors.extend(str(item) for item in verification.get("errors", []))
    if not verification.get("ok"):
        return list(dict.fromkeys(errors or ["Release archive verification failed"]))
    try:
        with zipfile.ZipFile(archive, "r") as bundle:
            manifest = json.loads(bundle.read(MANIFEST_NAME))
            files = manifest.get("files")
            if not isinstance(files, Mapping):
                return list(dict.fromkeys(errors + ["Release manifest file map is invalid"]))
            for path, markers in REQUIRED_FILES.items():
                if path not in files:
                    errors.append(f"Governance-capable release is missing file: {path}")
                    continue
                content = bundle.read(path).decode("utf-8")
                for marker in markers:
                    if marker not in content:
                        errors.append(
                            f"Governance-capable release marker is missing in {path}: {marker}"
                        )
    except (
        OSError,
        KeyError,
        UnicodeDecodeError,
        zipfile.BadZipFile,
        json.JSONDecodeError,
    ) as exc:
        errors.append(f"Unable to inspect repository governance release capability: {exc}")
    return list(dict.fromkeys(errors))


def require_current_governance_release(root: Path = ROOT) -> dict[str, Any]:
    state_path = root / "deploy/release/runtime/current_release.json"
    state = _load_json(state_path)
    archive_raw = str(state.get("archive", "")).strip()
    if not archive_raw:
        raise ValueError("Current release archive path is missing")
    archive = Path(archive_raw)
    if not archive.is_absolute():
        archive = root / archive
    errors = inspect_governance_release(archive)
    if errors:
        raise ValueError(
            "Current release is not governance-capable: " + "; ".join(errors)
        )
    verification = verify_release(archive)
    for key in ("release_id", "git_commit", "sha256"):
        if str(state.get(key, "")) != str(verification.get(key, "")):
            raise ValueError(f"Current release pointer {key} does not match archive")
    return state
