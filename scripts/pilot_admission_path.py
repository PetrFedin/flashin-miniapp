#!/usr/bin/env python3
"""Validate one exact signed pilot admission path against current release state."""

from __future__ import annotations

import os
from pathlib import Path

from pilot_admission import (
    _settings,
    load_verified_release_state,
    validate_admission_manifest,
)
from pilot_evidence import load_json
from pilot_operator_security import validate_application_token_isolation
from pilot_readiness import read_env


def verify_admission_path(manifest_path: Path, root: Path) -> list[str]:
    """Verify the exact manifest requested by the operator; never substitute default."""
    isolation_errors = validate_application_token_isolation(root, os.environ)
    if isolation_errors:
        return isolation_errors
    env = read_env(root / ".env")
    try:
        settings = _settings(env)
        current = load_verified_release_state(
            root / "deploy/release/runtime/current_release.json"
        )
        previous = load_verified_release_state(
            root / "deploy/release/runtime/previous_release.json"
        )
        manifest = load_json(manifest_path)
    except (OSError, ValueError) as exc:
        return [str(exc)]
    return validate_admission_manifest(
        manifest,
        env=env,
        current_release=current,
        previous_release=previous,
        admission_max_age_minutes=settings["admission"],
        provider_max_age_minutes=settings["provider"],
        live_max_age_minutes=settings["live"],
        rollback_max_age_days=settings["rollback"],
    )
