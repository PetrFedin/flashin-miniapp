#!/usr/bin/env python3
"""Configure FLASHIN main branch protection for controlled-pilot governance.

The command is intentionally operator-only and refuses to read a GitHub token
from the application .env. Inject a short-lived PILOT_GITHUB_TOKEN into this one
process. Updating protection requires repository Administration: write.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import quote

try:
    from .pilot_operator_security import require_privileged_token_file_isolation
    from .pilot_readiness import read_env
except ImportError:  # Direct execution: python scripts/configure_main_protection.py
    from pilot_operator_security import require_privileged_token_file_isolation
    from pilot_readiness import read_env

ROOT = Path(__file__).resolve().parents[1]
API_ROOT = "https://api.github.com"
API_VERSION = "2026-03-10"
DEFAULT_ACTIONS_APP_ID = 15368
DEFAULT_CHECKS = (
    "backend",
    "frontend",
    "admin",
    "browser-e2e",
    "integrated-e2e",
    "docker",
)


def _csv(value: object) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _positive_int(value: object, name: str) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed


def load_target(env: Mapping[str, str]) -> tuple[str, str, list[str], int]:
    repository = str(env.get("PILOT_GITHUB_REPOSITORY", "PetrFedin/flashin-miniapp")).strip()
    if repository.count("/") != 1 or any(not part.strip() for part in repository.split("/", 1)):
        raise ValueError("PILOT_GITHUB_REPOSITORY must be owner/repository")
    branch = str(env.get("PILOT_GITHUB_PROTECTED_BRANCH", "main")).strip()
    if not branch or any(char.isspace() for char in branch):
        raise ValueError("PILOT_GITHUB_PROTECTED_BRANCH is invalid")
    checks = _csv(env.get("PILOT_GITHUB_REQUIRED_CHECKS")) or list(DEFAULT_CHECKS)
    if checks != list(DEFAULT_CHECKS):
        raise ValueError(
            "PILOT_GITHUB_REQUIRED_CHECKS must exactly equal " + ",".join(DEFAULT_CHECKS)
        )
    app_id = _positive_int(
        env.get("PILOT_GITHUB_ACTIONS_APP_ID", DEFAULT_ACTIONS_APP_ID),
        "PILOT_GITHUB_ACTIONS_APP_ID",
    )
    if app_id != DEFAULT_ACTIONS_APP_ID:
        raise ValueError(f"PILOT_GITHUB_ACTIONS_APP_ID must be {DEFAULT_ACTIONS_APP_ID}")
    return repository, branch, checks, app_id


def build_protection_payload(checks: Sequence[str], app_id: int) -> dict[str, Any]:
    if list(checks) != list(DEFAULT_CHECKS):
        raise ValueError("Required checks do not match the controlled-pilot policy")
    if int(app_id) != DEFAULT_ACTIONS_APP_ID:
        raise ValueError("GitHub Actions App ID does not match the controlled-pilot policy")
    return {
        "required_status_checks": {
            "strict": True,
            "contexts": [],
            "checks": [
                {"context": context, "app_id": int(app_id)}
                for context in checks
            ],
        },
        "enforce_admins": True,
        # A PR is mandatory, but this single-owner repository does not require a
        # self-impossible approving reviewer. The six trusted CI checks remain the
        # machine approval boundary.
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": True,
            "require_code_owner_reviews": False,
            "required_approving_review_count": 0,
            "require_last_push_approval": False,
        },
        "restrictions": None,
        "required_linear_history": False,
        "allow_force_pushes": False,
        "allow_deletions": False,
        "block_creations": False,
        "required_conversation_resolution": True,
        "lock_branch": False,
        "allow_fork_syncing": False,
    }


def _request_json(
    method: str,
    url: str,
    *,
    token: str,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "flashin-pilot-branch-protection/1.0",
            **({"Content-Type": "application/json"} if data is not None else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GitHub branch-protection API returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"GitHub branch-protection API failed: {exc.__class__.__name__}") from exc
    try:
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError("GitHub branch-protection API returned invalid JSON") from exc
    if not isinstance(body, dict):
        raise RuntimeError("GitHub branch-protection API returned an invalid object")
    return body


def summarize_protection(body: Mapping[str, Any], expected_checks: Sequence[str], app_id: int) -> dict[str, Any]:
    status = body.get("required_status_checks")
    status = status if isinstance(status, Mapping) else {}
    raw_checks = status.get("checks")
    raw_checks = raw_checks if isinstance(raw_checks, list) else []
    normalized_checks = [
        {
            "context": str(item.get("context") or ""),
            "app_id": item.get("app_id"),
        }
        for item in raw_checks
        if isinstance(item, Mapping)
    ]
    enforce_admins = body.get("enforce_admins")
    enforce_admins = enforce_admins if isinstance(enforce_admins, Mapping) else {}
    force = body.get("allow_force_pushes")
    force = force if isinstance(force, Mapping) else {}
    deletions = body.get("allow_deletions")
    deletions = deletions if isinstance(deletions, Mapping) else {}
    conversations = body.get("required_conversation_resolution")
    conversations = conversations if isinstance(conversations, Mapping) else {}
    reviews = body.get("required_pull_request_reviews")
    reviews = reviews if isinstance(reviews, Mapping) else {}

    expected = [{"context": name, "app_id": app_id} for name in expected_checks]
    checks_ok = normalized_checks == expected
    summary = {
        "strict": status.get("strict") is True,
        "checks": normalized_checks,
        "checks_match_policy": checks_ok,
        "enforce_admins": enforce_admins.get("enabled") is True,
        "pull_request_required": bool(reviews),
        "required_approving_review_count": reviews.get("required_approving_review_count"),
        "allow_force_pushes": force.get("enabled") is True,
        "allow_deletions": deletions.get("enabled") is True,
        "required_conversation_resolution": conversations.get("enabled") is True,
    }
    summary["go"] = all(
        (
            summary["strict"],
            summary["checks_match_policy"],
            summary["enforce_admins"],
            summary["pull_request_required"],
            summary["required_approving_review_count"] == 0,
            not summary["allow_force_pushes"],
            not summary["allow_deletions"],
            summary["required_conversation_resolution"],
        )
    )
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the exact controlled-pilot branch protection. Without this flag, print a dry-run plan.",
    )
    parser.add_argument("--env", default=str(ROOT / ".env"))
    args = parser.parse_args(list(argv) if argv is not None else None)

    require_privileged_token_file_isolation(ROOT)
    env = read_env(Path(args.env))
    repository, branch, checks, app_id = load_target(env)
    payload = build_protection_payload(checks, app_id)

    safe_plan = {
        "repository": repository,
        "branch": branch,
        "checks": checks,
        "actions_app_id": app_id,
        "strict": True,
        "enforce_admins": True,
        "pull_request_required": True,
        "required_approving_review_count": 0,
        "allow_force_pushes": False,
        "allow_deletions": False,
        "required_conversation_resolution": True,
    }
    if not args.apply:
        print(json.dumps({"apply": False, "plan": safe_plan}, ensure_ascii=False, indent=2))
        return 0

    token = str(os.getenv("PILOT_GITHUB_TOKEN", "")).strip()
    if not token:
        print(json.dumps({"go": False, "error": "PILOT_GITHUB_TOKEN is required for --apply"}))
        return 1

    owner, repo = repository.split("/", 1)
    protection_url = (
        f"{API_ROOT}/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
        f"/branches/{quote(branch, safe='')}/protection"
    )
    try:
        body = _request_json("PUT", protection_url, token=token, payload=payload)
        summary = summarize_protection(body, checks, app_id)
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"go": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    print(json.dumps({"plan": safe_plan, "result": summary}, ensure_ascii=False, indent=2))
    return 0 if summary.get("go") else 1


if __name__ == "__main__":
    raise SystemExit(main())
