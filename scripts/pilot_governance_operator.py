#!/usr/bin/env python3
"""Operator-only entrypoint for GitHub governance evidence creation and verification."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Sequence

import pilot_repository_governance
from pilot_governance_policy import (
    TRUSTED_ACTIONS_APP_ID,
    TRUSTED_BRANCH,
    TRUSTED_REPOSITORY,
    TRUSTED_REQUIRED_CHECKS,
    TRUSTED_SECURITY_REQUIRED_JOBS,
    TRUSTED_SECURITY_WORKFLOW_API_PATH,
    TRUSTED_SECURITY_WORKFLOW_CONFIG_PATH,
    TRUSTED_SECURITY_WORKFLOW_NAME,
    TRUSTED_WORKFLOW_CONFIG_PATH,
    TRUSTED_WORKFLOW_NAME,
    report_trust_anchor_errors,
    require_trusted_configuration,
    trusted_security_workflow_candidates,
    trusted_security_workflow_job_evidence,
    trusted_workflow_candidates,
    trusted_workflow_job_evidence,
)
from pilot_operator_security import require_privileged_token_file_isolation

ROOT = Path(__file__).resolve().parents[1]


def _fail(errors: Sequence[str]) -> int:
    print(json.dumps({"go": False, "errors": list(errors)}, ensure_ascii=False))
    return 1


def _trusted_env(env: Mapping[str, str]) -> dict[str, str]:
    normalized = {str(key): str(value) for key, value in env.items()}
    normalized.setdefault("PILOT_GITHUB_REPOSITORY", TRUSTED_REPOSITORY)
    normalized.setdefault("PILOT_GITHUB_PROTECTED_BRANCH", TRUSTED_BRANCH)
    normalized.setdefault(
        "PILOT_GITHUB_REQUIRED_CHECKS", ",".join(TRUSTED_REQUIRED_CHECKS)
    )
    normalized.setdefault("PILOT_GITHUB_ACTIONS_APP_ID", str(TRUSTED_ACTIONS_APP_ID))
    normalized.setdefault("PILOT_GITHUB_WORKFLOW_NAME", TRUSTED_WORKFLOW_NAME)
    normalized.setdefault("PILOT_GITHUB_WORKFLOW_PATH", TRUSTED_WORKFLOW_CONFIG_PATH)
    return normalized


def _bind_trusted_job_evidence(
    report: Mapping[str, object],
    *,
    jobs: object,
    env: Mapping[str, str],
) -> dict[str, object]:
    unsigned = dict(report)
    unsigned.pop("signature", None)
    workflow = unsigned.get("workflow")
    if not isinstance(workflow, Mapping):
        raise ValueError("Repository governance workflow evidence is missing")
    workflow_with_jobs = dict(workflow)
    workflow_with_jobs["required_jobs"] = trusted_workflow_job_evidence(jobs)
    unsigned["workflow"] = workflow_with_jobs
    secret = pilot_repository_governance.require_signing_secret(env)
    return pilot_repository_governance.sign_payload(unsigned, secret)


def _bind_trusted_security_evidence(
    report: Mapping[str, object],
    *,
    workflow_run: Mapping[str, object],
    jobs: object,
    env: Mapping[str, str],
) -> dict[str, object]:
    unsigned = dict(report)
    unsigned.pop("signature", None)
    unsigned["security_workflow"] = {
        "id": workflow_run.get("id"),
        "name": workflow_run.get("name"),
        "path": workflow_run.get("path"),
        "event": workflow_run.get("event"),
        "status": workflow_run.get("status"),
        "conclusion": workflow_run.get("conclusion"),
        "head_sha": workflow_run.get("head_sha"),
        "html_url": workflow_run.get("html_url"),
        "created_at": workflow_run.get("created_at"),
        "updated_at": workflow_run.get("updated_at"),
        "required_jobs": trusted_security_workflow_job_evidence(jobs),
    }
    secret = pilot_repository_governance.require_signing_secret(env)
    return pilot_repository_governance.sign_payload(unsigned, secret)


def _render_operator_markdown(report: Mapping[str, object]) -> str:
    base = pilot_repository_governance.render_markdown(report).rstrip()
    security = report.get("security_workflow")
    if not isinstance(security, Mapping):
        return base + "\n"
    required_jobs = security.get("required_jobs")
    job_lines = []
    if isinstance(required_jobs, Mapping):
        job_lines = [
            f"- `{name}`: {required_jobs.get(name)}"
            for name in sorted(str(item) for item in required_jobs.keys())
        ]
    return "\n".join(
        [
            base,
            "",
            "## Supply-chain Security",
            "",
            f"Workflow: `{security.get('name', 'unknown')}` / `{security.get('id', 'unknown')}`",
            f"Path: `{security.get('path', 'unknown')}`",
            f"Event: `{security.get('event', 'unknown')}`",
            f"Head: `{security.get('head_sha', 'unknown')}`",
            f"Conclusion: `{security.get('conclusion', 'unknown')}`",
            "",
            "### Required Security jobs",
            "",
            *job_lines,
            "",
        ]
    )


def _workflow_jobs(run_id: int, *, token: str) -> object:
    payload = pilot_repository_governance._api_json(
        f"https://api.github.com/repos/{TRUSTED_REPOSITORY}/actions/runs/{run_id}/jobs?per_page=100",
        token=token,
    )
    return payload.get("jobs") if isinstance(payload, Mapping) else None


def _security_workflow_runs(*, token: str) -> object:
    payload = pilot_repository_governance._api_json(
        "https://api.github.com/repos/"
        f"{TRUSTED_REPOSITORY}/actions/workflows/{TRUSTED_SECURITY_WORKFLOW_CONFIG_PATH}/runs"
        f"?branch={TRUSTED_BRANCH}&status=completed&per_page=100",
        token=token,
    )
    return payload.get("workflow_runs") if isinstance(payload, Mapping) else None


def _verify_report(
    report_path: Path,
    *,
    env: Mapping[str, str],
) -> int:
    try:
        current = pilot_repository_governance.load_verified_release_state(
            ROOT / "deploy/release/runtime/current_release.json"
        )
        report = pilot_repository_governance.load_json(report_path)
        errors = pilot_repository_governance.validate_report(
            report,
            env=env,
            expected_release=current,
        )
        errors.extend(report_trust_anchor_errors(report))
        errors = list(dict.fromkeys(errors))
        print(json.dumps({"go": not errors, "errors": errors}, ensure_ascii=False))
        return 0 if not errors else 1
    except (OSError, ValueError) as exc:
        return _fail([str(exc)])


def main(argv: Sequence[str] | None = None) -> int:
    # This is the only supported process that may receive PILOT_GITHUB_TOKEN.
    # The application .env must remain token-free even while this command runs.
    require_privileged_token_file_isolation(ROOT)
    env = pilot_repository_governance._runtime_env(ROOT)
    try:
        require_trusted_configuration(env)
    except ValueError as exc:
        return _fail([str(exc)])
    effective_env = _trusted_env(env)

    args = pilot_repository_governance.build_parser().parse_args(argv)
    if args.command == "verify":
        return _verify_report(args.report, env=effective_env)

    try:
        current = pilot_repository_governance.load_verified_release_state(
            ROOT / "deploy/release/runtime/current_release.json"
        )
        snapshot = pilot_repository_governance.collect_snapshot(
            effective_env,
            current_release=current,
        )
        release_commit = str(current.get("git_commit", ""))
        candidates = trusted_workflow_candidates(
            snapshot.get("workflow_runs"),
            release_commit=release_commit,
        )
        if not candidates:
            return _fail(
                ["GitHub CI has no trusted successful protected-main push run for the current release commit"]
            )

        token = pilot_repository_governance._github_token(effective_env)
        security_candidates = trusted_security_workflow_candidates(
            _security_workflow_runs(token=token),
            release_commit=release_commit,
        )
        if not security_candidates:
            return _fail(
                ["GitHub Security has no trusted successful protected-main push run for the current release commit"]
            )

        selected = candidates[0]
        security_selected = security_candidates[0]
        run_id = int(selected["id"])
        security_run_id = int(security_selected["id"])
        jobs = _workflow_jobs(run_id, token=token)
        security_jobs = _workflow_jobs(security_run_id, token=token)

        bounded_snapshot = dict(snapshot)
        bounded_snapshot["workflow_runs"] = candidates
        report = pilot_repository_governance.build_report(
            bounded_snapshot,
            env=effective_env,
            current_release=current,
            owner=args.owner,
        )
        report = _bind_trusted_job_evidence(
            report,
            jobs=jobs,
            env=effective_env,
        )
        report = _bind_trusted_security_evidence(
            report,
            workflow_run=security_selected,
            jobs=security_jobs,
            env=effective_env,
        )
        trust_errors = report_trust_anchor_errors(report)
        if trust_errors:
            return _fail(trust_errors)

        pilot_repository_governance.atomic_write_json(args.report, report)
        pilot_repository_governance.atomic_write_text(
            args.report.with_suffix(".md"),
            _render_operator_markdown(report),
        )
        print(
            json.dumps(
                {
                    "go": True,
                    "report": str(args.report),
                    "workflow_run_id": run_id,
                    "security_workflow_run_id": security_run_id,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (OSError, ValueError) as exc:
        return _fail([str(exc)])


if __name__ == "__main__":
    raise SystemExit(main())
