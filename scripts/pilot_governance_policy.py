#!/usr/bin/env python3
"""Immutable trust anchors for FLASHIN repository-governance evidence."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

TRUSTED_REPOSITORY = "PetrFedin/flashin-miniapp"
TRUSTED_BRANCH = "main"
TRUSTED_REQUIRED_CHECKS = (
    "backend",
    "frontend",
    "admin",
    "browser-e2e",
    "integrated-e2e",
    "docker",
)
TRUSTED_ACTIONS_APP_ID = 15368
TRUSTED_WORKFLOW_NAME = "CI"
TRUSTED_WORKFLOW_CONFIG_PATH = "ci.yml"
TRUSTED_WORKFLOW_API_PATH = ".github/workflows/ci.yml"


def _csv(value: object) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(item.strip() for item in str(value or "").split(",") if item.strip())
    )


def trusted_configuration_errors(env: Mapping[str, str]) -> list[str]:
    """Reject any explicit configuration that can weaken the pilot GitHub trust boundary."""

    errors: list[str] = []
    repository = str(
        env.get("PILOT_GITHUB_REPOSITORY")
        or env.get("GITHUB_REPOSITORY")
        or TRUSTED_REPOSITORY
    ).strip()
    branch = str(env.get("PILOT_GITHUB_PROTECTED_BRANCH", TRUSTED_BRANCH)).strip()
    checks = _csv(
        env.get("PILOT_GITHUB_REQUIRED_CHECKS", ",".join(TRUSTED_REQUIRED_CHECKS))
    )
    workflow_name = str(
        env.get("PILOT_GITHUB_WORKFLOW_NAME", TRUSTED_WORKFLOW_NAME)
    ).strip()
    workflow_path = str(
        env.get("PILOT_GITHUB_WORKFLOW_PATH", TRUSTED_WORKFLOW_CONFIG_PATH)
    ).strip()
    app_id_raw = str(
        env.get("PILOT_GITHUB_ACTIONS_APP_ID", TRUSTED_ACTIONS_APP_ID)
    ).strip()

    if repository != TRUSTED_REPOSITORY:
        errors.append(f"PILOT_GITHUB_REPOSITORY must be {TRUSTED_REPOSITORY}")
    if branch != TRUSTED_BRANCH:
        errors.append(f"PILOT_GITHUB_PROTECTED_BRANCH must be {TRUSTED_BRANCH}")
    if checks != TRUSTED_REQUIRED_CHECKS:
        errors.append(
            "PILOT_GITHUB_REQUIRED_CHECKS must exactly equal "
            + ",".join(TRUSTED_REQUIRED_CHECKS)
        )
    try:
        app_id = int(app_id_raw)
    except ValueError:
        app_id = -1
    if app_id != TRUSTED_ACTIONS_APP_ID:
        errors.append(
            f"PILOT_GITHUB_ACTIONS_APP_ID must be {TRUSTED_ACTIONS_APP_ID}"
        )
    if workflow_name != TRUSTED_WORKFLOW_NAME:
        errors.append(f"PILOT_GITHUB_WORKFLOW_NAME must be {TRUSTED_WORKFLOW_NAME}")
    if workflow_path != TRUSTED_WORKFLOW_CONFIG_PATH:
        errors.append(
            f"PILOT_GITHUB_WORKFLOW_PATH must be {TRUSTED_WORKFLOW_CONFIG_PATH}"
        )
    return list(dict.fromkeys(errors))


def require_trusted_configuration(env: Mapping[str, str]) -> None:
    errors = trusted_configuration_errors(env)
    if errors:
        raise ValueError("Repository governance trust anchor mismatch: " + "; ".join(errors))


def trusted_workflow_candidates(
    workflow_runs: object,
    *,
    release_commit: str,
) -> list[Mapping[str, Any]]:
    """Return only exact trusted protected-main push CI candidates for one release SHA."""

    if not isinstance(workflow_runs, list):
        return []
    candidates = [
        item
        for item in workflow_runs
        if isinstance(item, Mapping)
        and isinstance(item.get("id"), int)
        and int(item.get("id", 0)) > 0
        and item.get("name") == TRUSTED_WORKFLOW_NAME
        and item.get("path") == TRUSTED_WORKFLOW_API_PATH
        and item.get("event") == "push"
        and item.get("status") == "completed"
        and item.get("conclusion") == "success"
        and item.get("head_sha") == release_commit
    ]
    return sorted(
        candidates,
        key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
        reverse=True,
    )


def trusted_workflow_job_errors(jobs: object) -> list[str]:
    """Require all six trusted jobs to complete successfully on the selected push run."""

    if not isinstance(jobs, list):
        return ["GitHub trusted workflow job evidence is missing"]
    errors: list[str] = []
    for name in TRUSTED_REQUIRED_CHECKS:
        matching = [
            item
            for item in jobs
            if isinstance(item, Mapping) and str(item.get("name", "")).strip() == name
        ]
        if not matching:
            errors.append(f"GitHub trusted workflow job is missing: {name}")
            continue
        if not any(
            item.get("status") == "completed" and item.get("conclusion") == "success"
            for item in matching
        ):
            errors.append(f"GitHub trusted workflow job is not successful: {name}")
    return errors


def _string_set(value: object) -> set[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return set()
    return {str(item).strip() for item in value if str(item).strip()}


def report_trust_anchor_errors(report: Mapping[str, Any]) -> list[str]:
    """Validate immutable trust anchors independently of mutable environment config."""

    errors: list[str] = []
    repository = report.get("repository")
    branch = report.get("branch")
    policy = report.get("policy")
    workflow = report.get("workflow")

    if not isinstance(repository, Mapping):
        errors.append("repository governance trusted repository evidence is missing")
    else:
        if repository.get("full_name") != TRUSTED_REPOSITORY:
            errors.append("repository governance repository is outside the trusted FLASHIN repository")
        if repository.get("default_branch") != TRUSTED_BRANCH:
            errors.append("repository governance default branch is outside the trusted main branch")

    if not isinstance(branch, Mapping):
        errors.append("repository governance trusted branch evidence is missing")
    elif branch.get("name") != TRUSTED_BRANCH:
        errors.append("repository governance branch is outside the trusted main branch")

    if not isinstance(policy, Mapping):
        errors.append("repository governance trusted policy evidence is missing")
    else:
        if policy.get("actions_app_id") != TRUSTED_ACTIONS_APP_ID:
            errors.append(
                "repository governance required checks are not bound to the trusted GitHub Actions app"
            )
        required_checks = _string_set(policy.get("required_checks"))
        if required_checks != set(TRUSTED_REQUIRED_CHECKS):
            errors.append("repository governance required checks do not match the immutable pilot gate")
        observed_sources = policy.get("observed_check_sources")
        if not isinstance(observed_sources, Mapping):
            errors.append("repository governance observed check sources are missing")
        else:
            for context in TRUSTED_REQUIRED_CHECKS:
                raw_sources = observed_sources.get(context)
                if not isinstance(raw_sources, list) or TRUSTED_ACTIONS_APP_ID not in raw_sources:
                    errors.append(
                        f"repository governance trusted check source is invalid: {context}"
                    )

    if not isinstance(workflow, Mapping):
        errors.append("repository governance trusted workflow evidence is missing")
    else:
        if workflow.get("name") != TRUSTED_WORKFLOW_NAME:
            errors.append("repository governance workflow is not the trusted CI workflow")
        if workflow.get("path") != TRUSTED_WORKFLOW_API_PATH:
            errors.append("repository governance workflow path is not the trusted CI workflow path")
        if workflow.get("event") != "push":
            errors.append("repository governance workflow must be an exact protected-main push run")

    return list(dict.fromkeys(errors))
