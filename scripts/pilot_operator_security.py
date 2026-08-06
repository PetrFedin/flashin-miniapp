#!/usr/bin/env python3
"""Protect privileged pilot operator credentials from application environments."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

FORBIDDEN_APPLICATION_ENV_KEYS = ("PILOT_GITHUB_TOKEN", "GITHUB_TOKEN")


def forbidden_application_env_keys(path: Path) -> list[str]:
    """Return privileged key names present in an application env file, even if empty."""
    if not path.is_file():
        return []
    found: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _value = line.split("=", 1)
        normalized = key.strip()
        if normalized in FORBIDDEN_APPLICATION_ENV_KEYS:
            found.append(normalized)
    return list(dict.fromkeys(found))


def forbidden_process_env_keys(env: Mapping[str, str]) -> list[str]:
    return [
        key
        for key in FORBIDDEN_APPLICATION_ENV_KEYS
        if str(env.get(key, "")).strip()
    ]


def validate_privileged_token_file_isolation(root: Path) -> list[str]:
    keys = forbidden_application_env_keys(root / ".env")
    if not keys:
        return []
    return [
        "Privileged GitHub operator token keys must not be stored in application .env: "
        + ", ".join(keys)
        + ". Remove the keys, restart application containers, and inject PILOT_GITHUB_TOKEN only into the governance-create process."
    ]


def validate_application_token_isolation(
    root: Path,
    process_env: Mapping[str, str],
) -> list[str]:
    errors = validate_privileged_token_file_isolation(root)
    process_keys = forbidden_process_env_keys(process_env)
    if process_keys:
        errors.append(
            "Privileged GitHub operator token is present in the application process environment: "
            + ", ".join(process_keys)
            + ". Remove it and restart the process before admission or pilot runtime operations."
        )
    return list(dict.fromkeys(errors))


def require_privileged_token_file_isolation(root: Path) -> None:
    errors = validate_privileged_token_file_isolation(root)
    if errors:
        raise ValueError("; ".join(errors))


def require_application_token_isolation(
    root: Path,
    process_env: Mapping[str, str],
) -> None:
    errors = validate_application_token_isolation(root, process_env)
    if errors:
        raise ValueError("; ".join(errors))
