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
    TRUSTED_WORKFLOW_CONFIG_PATH,
    TRUSTED_WORKFLOW_NAME,
    report_trust_anchor_errors,
    require_trusted_configuration,
    trusted_workflow_candidates,
    trusted_workflow_job_errors,
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
    if args.command != "create":
        return pilot_repository_governance.main(argv)

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

        selected = candidates[0]
        run_id = int(selected["id"])
        token = pilot_repository_governance._github_token(effective_env)
        jobs_payload = pilot_repository_governance._api_json(
            f"https://api.github.com/repos/{TRUSTED_REPOSITORY}/actions/runs/{run_id}/jobs?per_page=100",
            token=token,
        )
        jobs = jobs_payload.get("jobs") if isinstance(jobs_payload, Mapping) else None
        job_errors = trusted_workflow_job_errors(jobs)
        if job_errors:
            return _fail(job_errors)

        bounded_snapshot = dict(snapshot)
        bounded_snapshot["workflow_runs"] = candidates
        report = pilot_repository_governance.build_report(
            bounded_snapshot,
            env=effective_env,
            current_release=current,
            owner=args.owner,
        )
        trust_errors = report_trust_anchor_errors(report)
        if trust_errors:
            return _fail(trust_errors)

        pilot_repository_governance.atomic_write_json(args.report, report)
        pilot_repository_governance.atomic_write_text(
            args.report.with_suffix(".md"),
            pilot_repository_governance.render_markdown(report),
        )
        print(
            json.dumps(
                {
                    "go": True,
                    "report": str(args.report),
                    "workflow_run_id": run_id,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (OSError, ValueError) as exc:
        return _fail([str(exc)])


if __name__ == "__main__":
    raise SystemExit(main())
