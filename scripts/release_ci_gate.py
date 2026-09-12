#!/usr/bin/env python3
"""Fail closed unless a manual release is built from protected, exact-green main."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any

REQUIRED_CI_JOBS = frozenset(
    {
        "backend",
        "frontend",
        "admin",
        "browser-e2e",
        "integrated-e2e",
        "docker",
    }
)
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _github_get(url: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "flashin-release-ci-gate/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GitHub API returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"GitHub API request failed: {exc.__class__.__name__}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub API returned a non-object response")
    return payload


def select_exact_successful_ci_run(
    runs_payload: Mapping[str, Any],
    *,
    expected_sha: str,
) -> Mapping[str, Any] | None:
    runs = runs_payload.get("workflow_runs")
    if not isinstance(runs, Sequence) or isinstance(runs, (str, bytes)):
        return None
    matches: list[Mapping[str, Any]] = []
    for run in runs:
        if not isinstance(run, Mapping):
            continue
        if (
            run.get("name") == "CI"
            and run.get("path") == ".github/workflows/ci.yml"
            and run.get("event") == "push"
            and run.get("head_branch") == "main"
            and run.get("head_sha") == expected_sha
            and run.get("status") == "completed"
            and run.get("conclusion") == "success"
            and isinstance(run.get("id"), int)
        ):
            matches.append(run)
    if not matches:
        return None
    return max(matches, key=lambda item: int(item.get("run_number") or 0))


def validate_required_jobs(jobs_payload: Mapping[str, Any]) -> list[str]:
    jobs = jobs_payload.get("jobs")
    if not isinstance(jobs, Sequence) or isinstance(jobs, (str, bytes)):
        return ["Exact CI jobs response is invalid"]

    by_name: dict[str, Mapping[str, Any]] = {}
    for job in jobs:
        if isinstance(job, Mapping) and isinstance(job.get("name"), str):
            by_name[str(job["name"])] = job

    errors: list[str] = []
    missing = sorted(REQUIRED_CI_JOBS - set(by_name))
    if missing:
        errors.append("Exact CI run is missing required jobs: " + ", ".join(missing))
    for name in sorted(REQUIRED_CI_JOBS & set(by_name)):
        job = by_name[name]
        if job.get("status") != "completed" or job.get("conclusion") != "success":
            errors.append(f"Required CI job {name} is not successful")
    return errors


def validate_release_context(
    *,
    ref_name: str,
    expected_sha: str,
    branch_payload: Mapping[str, Any],
    runs_payload: Mapping[str, Any],
    jobs_payload: Mapping[str, Any] | None,
) -> tuple[list[str], Mapping[str, Any] | None]:
    errors: list[str] = []
    if ref_name != "main":
        errors.append("Release artifacts may only be created from main")
    if not _SHA_RE.fullmatch(expected_sha):
        errors.append("GITHUB_SHA must be a full lowercase commit SHA")
    if branch_payload.get("name") != "main":
        errors.append("GitHub branch response is not main")

    commit = branch_payload.get("commit")
    branch_sha = commit.get("sha") if isinstance(commit, Mapping) else None
    if branch_sha != expected_sha:
        errors.append("Release SHA is not the current main head")
    if branch_payload.get("protected") is not True:
        errors.append("main is not protected")

    exact_run = select_exact_successful_ci_run(runs_payload, expected_sha=expected_sha)
    if exact_run is None:
        errors.append("No successful exact-SHA push CI run exists for main")
    elif jobs_payload is None:
        errors.append("Exact CI job evidence is missing")
    else:
        errors.extend(validate_required_jobs(jobs_payload))
    return list(dict.fromkeys(errors)), exact_run


def main() -> int:
    token = os.getenv("GITHUB_TOKEN", "").strip()
    repository = os.getenv("GITHUB_REPOSITORY", "").strip()
    sha = os.getenv("GITHUB_SHA", "").strip().lower()
    ref_name = os.getenv("GITHUB_REF_NAME", "").strip()
    api_base = os.getenv("GITHUB_API_URL", "https://api.github.com").strip().rstrip("/")

    if not token:
        print(json.dumps({"ok": False, "errors": ["GITHUB_TOKEN is required"]}))
        return 1
    if not _REPOSITORY_RE.fullmatch(repository):
        print(json.dumps({"ok": False, "errors": ["GITHUB_REPOSITORY is invalid"]}))
        return 1

    encoded_sha = urllib.parse.quote(sha, safe="")
    branch = _github_get(f"{api_base}/repos/{repository}/branches/main", token)
    runs = _github_get(
        f"{api_base}/repos/{repository}/actions/runs"
        f"?head_sha={encoded_sha}&event=push&status=completed&per_page=100",
        token,
    )
    exact_run = select_exact_successful_ci_run(runs, expected_sha=sha)
    jobs: Mapping[str, Any] | None = None
    if exact_run is not None:
        jobs = _github_get(
            f"{api_base}/repos/{repository}/actions/runs/{int(exact_run['id'])}/jobs?per_page=100",
            token,
        )

    errors, exact_run = validate_release_context(
        ref_name=ref_name,
        expected_sha=sha,
        branch_payload=branch,
        runs_payload=runs,
        jobs_payload=jobs,
    )
    print(
        json.dumps(
            {
                "ok": not errors,
                "repository": repository,
                "branch": "main",
                "sha": sha if _SHA_RE.fullmatch(sha) else None,
                "branch_protected": branch.get("protected") is True,
                "exact_push_ci_run_id": exact_run.get("id") if exact_run else None,
                "required_jobs": sorted(REQUIRED_CI_JOBS),
                "errors": errors,
            },
            ensure_ascii=False,
        )
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
