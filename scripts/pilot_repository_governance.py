#!/usr/bin/env python3
"""Create and verify signed GitHub repository-governance evidence for pilot admission."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from pilot_admission import load_verified_release_state
from pilot_evidence import (
    atomic_write_json,
    atomic_write_text,
    configuration_fingerprint,
    load_json,
    release_binding,
    require_signing_secret,
    sign_payload,
    utc_now,
    utc_timestamp,
    validate_evidence_window,
    validate_release_binding,
    verify_payload_signature,
)
from pilot_readiness import read_env

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "docs/pilot/repository_governance_report.json"
API_VERSION = "2026-03-10"
DEFAULT_REQUIRED_CHECKS = (
    "backend",
    "frontend",
    "admin",
    "browser-e2e",
    "integrated-e2e",
    "docker",
)
DEFAULT_WORKFLOW_NAME = "CI"
DEFAULT_WORKFLOW_PATH = "ci.yml"
DEFAULT_GITHUB_ACTIONS_APP_ID = 15368


def _positive_int(
    env: Mapping[str, str], key: str, default: int, minimum: int, maximum: int
) -> int:
    raw = str(env.get(key, default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if value < minimum or value > maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return value


def _csv(value: object) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(item.strip() for item in str(value or "").split(",") if item.strip())
    )


def settings(env: Mapping[str, str]) -> dict[str, Any]:
    repository = str(
        env.get("PILOT_GITHUB_REPOSITORY") or env.get("GITHUB_REPOSITORY") or ""
    ).strip()
    if repository.count("/") != 1:
        raise ValueError("PILOT_GITHUB_REPOSITORY must use owner/repository format")
    branch = str(env.get("PILOT_GITHUB_PROTECTED_BRANCH", "main")).strip()
    if not branch or branch.startswith("refs/"):
        raise ValueError("PILOT_GITHUB_PROTECTED_BRANCH must be a branch name")
    required_checks = _csv(
        env.get("PILOT_GITHUB_REQUIRED_CHECKS", ",".join(DEFAULT_REQUIRED_CHECKS))
    )
    if not required_checks:
        raise ValueError("PILOT_GITHUB_REQUIRED_CHECKS must not be empty")
    workflow_name = str(
        env.get("PILOT_GITHUB_WORKFLOW_NAME", DEFAULT_WORKFLOW_NAME)
    ).strip()
    workflow_path = str(
        env.get("PILOT_GITHUB_WORKFLOW_PATH", DEFAULT_WORKFLOW_PATH)
    ).strip()
    if not workflow_name or not workflow_path:
        raise ValueError("PILOT_GITHUB_WORKFLOW_NAME and PILOT_GITHUB_WORKFLOW_PATH are required")
    return {
        "repository": repository,
        "branch": branch,
        "required_checks": required_checks,
        "workflow_name": workflow_name,
        "workflow_path": workflow_path,
        "actions_app_id": _positive_int(
            env,
            "PILOT_GITHUB_ACTIONS_APP_ID",
            DEFAULT_GITHUB_ACTIONS_APP_ID,
            1,
            2_147_483_647,
        ),
        "max_age_minutes": _positive_int(
            env,
            "PILOT_GITHUB_GOVERNANCE_MAX_AGE_MINUTES",
            60,
            5,
            240,
        ),
    }


def _github_token(env: Mapping[str, str]) -> str:
    token = str(env.get("PILOT_GITHUB_TOKEN") or env.get("GITHUB_TOKEN") or "").strip()
    if not token:
        raise ValueError(
            "PILOT_GITHUB_TOKEN is required to read branch protection and ruleset bypass data"
        )
    return token


def _runtime_env(root: Path = ROOT) -> dict[str, str]:
    """Load app configuration without persisting the privileged operator token."""
    file_env = read_env(root / ".env")
    process_env = {str(key): str(value) for key, value in os.environ.items()}
    if not file_env:
        return process_env
    merged = dict(file_env)
    for key in ("PILOT_GITHUB_TOKEN", "GITHUB_TOKEN"):
        value = process_env.get(key, "").strip()
        if value:
            merged[key] = value
    return merged


def _api_json(url: str, *, token: str, allow_not_found: bool = False) -> Any:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "flashin-pilot-governance",
        "X-GitHub-Api-Version": API_VERSION,
    }
    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed GitHub API host
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 404 and allow_not_found:
            return None
        try:
            details = exc.read().decode("utf-8")
        except OSError:
            details = ""
        raise ValueError(f"GitHub API returned HTTP {exc.code}: {details[:300]}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ValueError(f"GitHub API request failed: {exc}") from exc


def collect_snapshot(
    env: Mapping[str, str],
    *,
    current_release: Mapping[str, Any],
) -> dict[str, Any]:
    config = settings(env)
    token = _github_token(env)
    owner, repository_name = config["repository"].split("/", 1)
    branch = config["branch"]
    base = f"https://api.github.com/repos/{quote(owner)}/{quote(repository_name)}"

    repository = _api_json(base, token=token)
    branch_payload = _api_json(f"{base}/branches/{quote(branch, safe='')}", token=token)
    protection = _api_json(
        f"{base}/branches/{quote(branch, safe='')}/protection",
        token=token,
        allow_not_found=True,
    )
    active_rules = _api_json(
        f"{base}/rules/branches/{quote(branch, safe='')}?per_page=100",
        token=token,
    )
    if not isinstance(active_rules, list):
        raise ValueError("GitHub active branch rules response must be a list")

    rulesets: list[dict[str, Any]] = []
    for ruleset_id in sorted(
        {
            int(item["ruleset_id"])
            for item in active_rules
            if isinstance(item, Mapping) and str(item.get("ruleset_id", "")).isdigit()
        }
    ):
        details = _api_json(f"{base}/rulesets/{ruleset_id}", token=token)
        if not isinstance(details, Mapping):
            raise ValueError(f"GitHub ruleset {ruleset_id} response must be an object")
        rulesets.append(dict(details))

    workflow_query = urlencode(
        {"branch": branch, "status": "completed", "per_page": 100}
    )
    workflow_path = quote(config["workflow_path"], safe="")
    workflow_runs = _api_json(
        f"{base}/actions/workflows/{workflow_path}/runs?{workflow_query}",
        token=token,
    )
    if not isinstance(workflow_runs, Mapping):
        raise ValueError("GitHub workflow runs response must be an object")

    return {
        "repository": repository,
        "branch": branch_payload,
        "protection": protection,
        "active_rules": active_rules,
        "rulesets": rulesets,
        "workflow_runs": workflow_runs.get("workflow_runs", []),
        "expected_release_commit": str(current_release.get("git_commit", "")),
    }


def _enabled(value: object) -> bool:
    return isinstance(value, Mapping) and value.get("enabled") is True


def _explicitly_disabled(value: object) -> bool:
    return isinstance(value, Mapping) and value.get("enabled") is False


def _record_source(
    sources: dict[str, set[int]],
    *,
    context: str,
    app_id: object,
) -> None:
    if isinstance(app_id, int):
        sources.setdefault(context, set()).add(app_id)


def _legacy_status_checks(
    protection: object,
) -> tuple[set[str], dict[str, set[int]], bool]:
    if not isinstance(protection, Mapping):
        return set(), {}, False
    status = protection.get("required_status_checks")
    if not isinstance(status, Mapping):
        return set(), {}, False
    contexts = {str(item) for item in status.get("contexts", []) if str(item).strip()}
    sources: dict[str, set[int]] = {}
    for item in status.get("checks", []):
        if not isinstance(item, Mapping):
            continue
        context = str(item.get("context", "")).strip()
        if not context:
            continue
        contexts.add(context)
        _record_source(sources, context=context, app_id=item.get("app_id"))
    return contexts, sources, status.get("strict") is True


def _ruleset_status_checks(
    active_rules: object,
) -> tuple[set[str], dict[str, set[int]], bool]:
    contexts: set[str] = set()
    sources: dict[str, set[int]] = {}
    strict = False
    if not isinstance(active_rules, list):
        return contexts, sources, strict
    for rule in active_rules:
        if not isinstance(rule, Mapping) or rule.get("type") != "required_status_checks":
            continue
        parameters = rule.get("parameters")
        if not isinstance(parameters, Mapping):
            continue
        strict = strict or parameters.get("strict_required_status_checks_policy") is True
        for item in parameters.get("required_status_checks", []):
            if not isinstance(item, Mapping):
                continue
            context = str(item.get("context", "")).strip()
            if not context:
                continue
            contexts.add(context)
            _record_source(
                sources,
                context=context,
                app_id=item.get("integration_id"),
            )
    return contexts, sources, strict


def _merge_sources(*source_maps: Mapping[str, set[int]]) -> dict[str, set[int]]:
    merged: dict[str, set[int]] = {}
    for source_map in source_maps:
        for context, app_ids in source_map.items():
            merged.setdefault(context, set()).update(app_ids)
    return merged


def _ruleset_bypass_state(rulesets: object) -> tuple[bool, list[dict[str, Any]]]:
    if not isinstance(rulesets, list):
        return False, []
    visible = True
    actors: list[dict[str, Any]] = []
    for ruleset in rulesets:
        if not isinstance(ruleset, Mapping):
            visible = False
            continue
        if "bypass_actors" not in ruleset or not isinstance(ruleset.get("bypass_actors"), list):
            visible = False
            continue
        for actor in ruleset.get("bypass_actors", []):
            if not isinstance(actor, Mapping):
                visible = False
                continue
            actors.append(
                {
                    "actor_type": actor.get("actor_type"),
                    "actor_id": actor.get("actor_id"),
                    "bypass_mode": actor.get("bypass_mode"),
                }
            )
    return visible, actors


def evaluate_snapshot(
    snapshot: Mapping[str, Any],
    *,
    config: Mapping[str, Any],
    current_release: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    repository = snapshot.get("repository")
    branch = snapshot.get("branch")
    protection = snapshot.get("protection")
    active_rules = snapshot.get("active_rules")
    rulesets = snapshot.get("rulesets")
    workflow_runs = snapshot.get("workflow_runs")

    if not isinstance(repository, Mapping):
        return {}, ["GitHub repository metadata is missing"]
    if repository.get("full_name") != config["repository"]:
        errors.append("GitHub repository full name does not match configuration")
    if repository.get("archived") is True:
        errors.append("GitHub repository is archived")
    if repository.get("default_branch") != config["branch"]:
        errors.append("protected pilot branch is not the repository default branch")

    if not isinstance(branch, Mapping):
        return {}, list(dict.fromkeys(errors + ["GitHub branch metadata is missing"]))
    head = branch.get("commit")
    head_sha = str(head.get("sha", "")) if isinstance(head, Mapping) else ""
    release_commit = str(current_release.get("git_commit", ""))
    if len(head_sha) != 40:
        errors.append("GitHub protected branch head SHA is invalid")
    if head_sha != release_commit:
        errors.append("GitHub protected branch head does not match current release commit")

    active_types = {
        str(item.get("type", ""))
        for item in active_rules or []
        if isinstance(item, Mapping)
    }
    legacy_contexts, legacy_sources, legacy_strict = _legacy_status_checks(protection)
    ruleset_contexts, ruleset_sources, ruleset_strict = _ruleset_status_checks(active_rules)
    contexts = legacy_contexts | ruleset_contexts
    check_sources = _merge_sources(legacy_sources, ruleset_sources)
    required_checks = set(config["required_checks"])
    actions_app_id = int(config["actions_app_id"])

    branch_protected = branch.get("protected") is True or bool(active_types)
    pull_request_required = (
        isinstance(protection, Mapping)
        and isinstance(protection.get("required_pull_request_reviews"), Mapping)
    ) or "pull_request" in active_types
    status_checks_required = required_checks.issubset(contexts)
    status_check_sources_required = all(
        actions_app_id in check_sources.get(context, set())
        for context in required_checks
    )
    strict_status_checks = legacy_strict or ruleset_strict
    force_push_blocked = (
        isinstance(protection, Mapping)
        and _explicitly_disabled(protection.get("allow_force_pushes"))
    ) or "non_fast_forward" in active_types
    deletion_blocked = (
        isinstance(protection, Mapping)
        and _explicitly_disabled(protection.get("allow_deletions"))
    ) or "deletion" in active_types
    admins_enforced = (
        isinstance(protection, Mapping) and _enabled(protection.get("enforce_admins"))
    )

    has_rulesets = isinstance(rulesets, list) and bool(rulesets)
    ruleset_bypass_visibility, bypass_actors = _ruleset_bypass_state(rulesets)
    if has_rulesets:
        administrator_bypass_blocked = ruleset_bypass_visibility and not bypass_actors
    elif isinstance(protection, Mapping):
        ruleset_bypass_visibility = True
        administrator_bypass_blocked = admins_enforced
    else:
        ruleset_bypass_visibility = False
        administrator_bypass_blocked = False

    checks = {
        "branch_protected": branch_protected,
        "pull_request_required": pull_request_required,
        "required_status_checks": status_checks_required,
        "required_status_check_sources": status_check_sources_required,
        "strict_status_checks": strict_status_checks,
        "force_push_blocked": force_push_blocked,
        "deletion_blocked": deletion_blocked,
        "ruleset_bypass_visibility": ruleset_bypass_visibility,
        "administrator_bypass_blocked": administrator_bypass_blocked,
    }
    for name, passed in checks.items():
        if passed is not True:
            errors.append(f"GitHub governance check failed: {name}")
    missing = sorted(required_checks - contexts)
    if missing:
        errors.append("GitHub required status checks are missing: " + ", ".join(missing))
    wrong_sources = sorted(
        context
        for context in required_checks
        if actions_app_id not in check_sources.get(context, set())
    )
    if wrong_sources:
        errors.append(
            "GitHub required status checks are not bound to the configured Actions app: "
            + ", ".join(wrong_sources)
        )
    if has_rulesets and not ruleset_bypass_visibility:
        errors.append(
            "GitHub ruleset bypass actors are not visible; the token lacks sufficient access"
        )
    if bypass_actors:
        errors.append("GitHub rulesets contain bypass actors")

    successful_runs: list[Mapping[str, Any]] = []
    if isinstance(workflow_runs, list):
        successful_runs = [
            item
            for item in workflow_runs
            if isinstance(item, Mapping)
            and item.get("name") == config["workflow_name"]
            and item.get("head_sha") == release_commit
            and item.get("conclusion") == "success"
            and item.get("status") == "completed"
            and item.get("event") in {"push", "pull_request"}
        ]
    if not successful_runs:
        errors.append("GitHub CI has no successful completed run for the current release commit")
        workflow: dict[str, Any] = {}
    else:
        selected = max(
            successful_runs,
            key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
        )
        workflow = {
            "id": selected.get("id"),
            "name": selected.get("name"),
            "path": selected.get("path"),
            "event": selected.get("event"),
            "status": selected.get("status"),
            "conclusion": selected.get("conclusion"),
            "head_sha": selected.get("head_sha"),
            "html_url": selected.get("html_url"),
            "created_at": selected.get("created_at"),
            "updated_at": selected.get("updated_at"),
        }

    normalized = {
        "github_api_version": API_VERSION,
        "repository": {
            "full_name": repository.get("full_name"),
            "default_branch": repository.get("default_branch"),
            "archived": repository.get("archived") is True,
        },
        "branch": {
            "name": config["branch"],
            "head_sha": head_sha,
            "protected": branch_protected,
        },
        "policy": {
            **checks,
            "actions_app_id": actions_app_id,
            "required_checks": sorted(required_checks),
            "observed_status_checks": sorted(contexts),
            "observed_check_sources": {
                context: sorted(app_ids)
                for context, app_ids in sorted(check_sources.items())
            },
            "active_rule_types": sorted(active_types),
            "ruleset_ids": sorted(
                {
                    int(item.get("id"))
                    for item in rulesets or []
                    if isinstance(item, Mapping) and str(item.get("id", "")).isdigit()
                }
            ),
            "bypass_actors": bypass_actors,
        },
        "workflow": workflow,
    }
    return normalized, list(dict.fromkeys(errors))


def build_report(
    snapshot: Mapping[str, Any],
    *,
    env: Mapping[str, str],
    current_release: Mapping[str, Any],
    owner: str,
    max_age_minutes: int | None = None,
    now=None,
) -> dict[str, Any]:
    config = settings(env)
    if not owner.strip():
        raise ValueError("Repository governance owner is required")
    normalized, errors = evaluate_snapshot(
        snapshot, config=config, current_release=current_release
    )
    if errors:
        raise ValueError("Repository governance is not GO: " + "; ".join(errors))
    secret = require_signing_secret(env)
    created = (now or utc_now()).astimezone(UTC)
    maximum_age = max_age_minutes or config["max_age_minutes"]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "kind": "pilot_repository_governance",
        "created_at": utc_timestamp(created),
        "expires_at": utc_timestamp(created + timedelta(minutes=maximum_age)),
        "decision": "GO",
        "configuration_fingerprint": configuration_fingerprint(env, secret),
        "release": release_binding(current_release),
        "owner": owner.strip(),
        **normalized,
    }
    return sign_payload(payload, secret)


def validate_report(
    report: Mapping[str, Any],
    *,
    env: Mapping[str, str],
    expected_release: Mapping[str, Any],
    max_age_minutes: int | None = None,
    now=None,
) -> list[str]:
    errors: list[str] = []
    try:
        config = settings(env)
        secret = require_signing_secret(env)
    except ValueError as exc:
        return [str(exc)]
    if report.get("schema_version") != 1:
        errors.append("unsupported repository governance evidence schema")
    if report.get("kind") != "pilot_repository_governance":
        errors.append("repository governance evidence kind is invalid")
    if report.get("decision") != "GO":
        errors.append("repository governance decision is not GO")
    if report.get("github_api_version") != API_VERSION:
        errors.append("repository governance GitHub API version is invalid")
    if not verify_payload_signature(report, secret):
        errors.append("repository governance evidence signature is invalid")
    if report.get("configuration_fingerprint") != configuration_fingerprint(env, secret):
        errors.append("repository governance configuration fingerprint does not match")
    release = report.get("release")
    if not isinstance(release, Mapping):
        errors.append("repository governance release binding is missing")
    else:
        errors.extend(validate_release_binding(release, expected_release))
    errors.extend(
        validate_evidence_window(
            report,
            now=now,
            maximum_age=timedelta(
                minutes=max_age_minutes or config["max_age_minutes"]
            ),
        )
    )

    repository = report.get("repository")
    branch = report.get("branch")
    policy = report.get("policy")
    workflow = report.get("workflow")
    if not isinstance(repository, Mapping):
        errors.append("repository governance repository evidence is missing")
    else:
        if repository.get("full_name") != config["repository"]:
            errors.append("repository governance repository does not match configuration")
        if repository.get("default_branch") != config["branch"]:
            errors.append("repository governance default branch does not match configuration")
        if repository.get("archived") is not False:
            errors.append("repository governance repository is archived")
    if not isinstance(branch, Mapping):
        errors.append("repository governance branch evidence is missing")
    else:
        if branch.get("name") != config["branch"]:
            errors.append("repository governance branch does not match configuration")
        if branch.get("head_sha") != expected_release.get("git_commit"):
            errors.append("repository governance branch head does not match current release")
        if branch.get("protected") is not True:
            errors.append("repository governance branch is not protected")

    required_policy_checks = (
        "branch_protected",
        "pull_request_required",
        "required_status_checks",
        "required_status_check_sources",
        "strict_status_checks",
        "force_push_blocked",
        "deletion_blocked",
        "ruleset_bypass_visibility",
        "administrator_bypass_blocked",
    )
    if not isinstance(policy, Mapping):
        errors.append("repository governance policy evidence is missing")
    else:
        for key in required_policy_checks:
            if policy.get(key) is not True:
                errors.append(f"repository governance policy is not passing: {key}")
        if policy.get("actions_app_id") != config["actions_app_id"]:
            errors.append("repository governance Actions app ID does not match configuration")
        if set(policy.get("required_checks", [])) != set(config["required_checks"]):
            errors.append("repository governance required checks do not match configuration")
        observed = {str(item) for item in policy.get("observed_status_checks", [])}
        if not set(config["required_checks"]).issubset(observed):
            errors.append("repository governance observed status checks are incomplete")
        observed_sources = policy.get("observed_check_sources")
        if not isinstance(observed_sources, Mapping):
            errors.append("repository governance observed check sources are missing")
        else:
            for context in config["required_checks"]:
                source_ids = observed_sources.get(context)
                if (
                    not isinstance(source_ids, list)
                    or config["actions_app_id"] not in source_ids
                ):
                    errors.append(
                        f"repository governance check source is invalid: {context}"
                    )
        if policy.get("bypass_actors"):
            errors.append("repository governance contains bypass actors")

    if not isinstance(workflow, Mapping):
        errors.append("repository governance workflow evidence is missing")
    else:
        if workflow.get("name") != config["workflow_name"]:
            errors.append("repository governance workflow name does not match")
        workflow_path = str(workflow.get("path", "")).strip()
        if not workflow_path or not workflow_path.endswith("/" + config["workflow_path"]):
            errors.append("repository governance workflow path does not match")
        if workflow.get("head_sha") != expected_release.get("git_commit"):
            errors.append("repository governance workflow head does not match current release")
        if workflow.get("status") != "completed" or workflow.get("conclusion") != "success":
            errors.append("repository governance workflow is not successfully completed")
        if not isinstance(workflow.get("id"), int) or workflow.get("id", 0) <= 0:
            errors.append("repository governance workflow run ID is invalid")
    if not str(report.get("owner", "")).strip():
        errors.append("repository governance owner is missing")
    return list(dict.fromkeys(errors))


def render_markdown(report: Mapping[str, Any]) -> str:
    repository = report.get("repository") if isinstance(report.get("repository"), Mapping) else {}
    branch = report.get("branch") if isinstance(report.get("branch"), Mapping) else {}
    policy = report.get("policy") if isinstance(report.get("policy"), Mapping) else {}
    workflow = report.get("workflow") if isinstance(report.get("workflow"), Mapping) else {}
    return "\n".join(
        [
            "# FLASHIN repository governance evidence",
            "",
            f"Decision: **{report.get('decision', 'NO-GO')}**",
            "",
            f"Repository: `{repository.get('full_name', 'unknown')}`",
            f"Branch: `{branch.get('name', 'unknown')}`",
            f"Head: `{branch.get('head_sha', 'unknown')}`",
            f"Workflow: `{workflow.get('name', 'unknown')}` / `{workflow.get('id', 'unknown')}`",
            f"Actions app ID: `{policy.get('actions_app_id', 'unknown')}`",
            f"Owner: `{report.get('owner', 'missing')}`",
            f"Created: `{report.get('created_at')}`",
            f"Expires: `{report.get('expires_at')}`",
            "",
            "## Policy",
            "",
            *[
                f"- `{key}`: {policy.get(key) is True}"
                for key in (
                    "branch_protected",
                    "pull_request_required",
                    "required_status_checks",
                    "required_status_check_sources",
                    "strict_status_checks",
                    "force_push_blocked",
                    "deletion_blocked",
                    "ruleset_bypass_visibility",
                    "administrator_bypass_blocked",
                )
            ],
            "",
        ]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="Query GitHub and create signed governance evidence")
    create.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    create.add_argument("--owner", required=True)
    verify = sub.add_parser("verify", help="Verify signed governance evidence")
    verify.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    env = _runtime_env(ROOT)
    try:
        current = load_verified_release_state(
            ROOT / "deploy/release/runtime/current_release.json"
        )
        if args.command == "create":
            snapshot = collect_snapshot(env, current_release=current)
            report = build_report(
                snapshot,
                env=env,
                current_release=current,
                owner=args.owner,
            )
            atomic_write_json(args.report, report)
            atomic_write_text(args.report.with_suffix(".md"), render_markdown(report))
            print(json.dumps({"go": True, "report": str(args.report)}, ensure_ascii=False))
            return 0
        report = load_json(args.report)
        errors = validate_report(report, env=env, expected_release=current)
        print(json.dumps({"go": not errors, "errors": errors}, ensure_ascii=False))
        return 0 if not errors else 1
    except (OSError, ValueError) as exc:
        print(json.dumps({"go": False, "errors": [str(exc)]}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
