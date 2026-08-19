from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot_governance_policy import (  # noqa: E402
    TRUSTED_ACTIONS_APP_ID,
    TRUSTED_BRANCH,
    TRUSTED_REPOSITORY,
    TRUSTED_REQUIRED_CHECKS,
    TRUSTED_WORKFLOW_API_PATH,
    TRUSTED_WORKFLOW_CONFIG_PATH,
    TRUSTED_WORKFLOW_NAME,
    report_trust_anchor_errors,
    require_trusted_configuration,
    trusted_workflow_candidates,
    trusted_workflow_job_evidence,
    trusted_workflow_job_errors,
)


def _env(**overrides):
    env = {
        "PILOT_GITHUB_REPOSITORY": TRUSTED_REPOSITORY,
        "PILOT_GITHUB_PROTECTED_BRANCH": TRUSTED_BRANCH,
        "PILOT_GITHUB_REQUIRED_CHECKS": ",".join(TRUSTED_REQUIRED_CHECKS),
        "PILOT_GITHUB_ACTIONS_APP_ID": str(TRUSTED_ACTIONS_APP_ID),
        "PILOT_GITHUB_WORKFLOW_NAME": TRUSTED_WORKFLOW_NAME,
        "PILOT_GITHUB_WORKFLOW_PATH": TRUSTED_WORKFLOW_CONFIG_PATH,
    }
    env.update(overrides)
    return env


def _required_jobs():
    return {name: "success" for name in TRUSTED_REQUIRED_CHECKS}


def _report(**workflow_overrides):
    workflow = {
        "id": 1001,
        "name": TRUSTED_WORKFLOW_NAME,
        "path": TRUSTED_WORKFLOW_API_PATH,
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "head_sha": "a" * 40,
        "required_jobs": _required_jobs(),
    }
    workflow.update(workflow_overrides)
    return {
        "repository": {
            "full_name": TRUSTED_REPOSITORY,
            "default_branch": TRUSTED_BRANCH,
            "archived": False,
        },
        "branch": {
            "name": TRUSTED_BRANCH,
            "head_sha": "a" * 40,
            "protected": True,
        },
        "policy": {
            "actions_app_id": TRUSTED_ACTIONS_APP_ID,
            "required_checks": list(TRUSTED_REQUIRED_CHECKS),
            "observed_check_sources": {
                name: [TRUSTED_ACTIONS_APP_ID] for name in TRUSTED_REQUIRED_CHECKS
            },
        },
        "workflow": workflow,
    }


def _workflow_run(**overrides):
    run = {
        "id": 1001,
        "name": TRUSTED_WORKFLOW_NAME,
        "path": TRUSTED_WORKFLOW_API_PATH,
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "head_sha": "a" * 40,
        "created_at": "2026-08-19T09:00:00Z",
        "updated_at": "2026-08-19T09:30:00Z",
    }
    run.update(overrides)
    return run


def _jobs():
    return [
        {"name": name, "status": "completed", "conclusion": "success"}
        for name in TRUSTED_REQUIRED_CHECKS
    ]


def test_trusted_governance_configuration_accepts_only_exact_policy():
    require_trusted_configuration(_env())
    require_trusted_configuration({"APP_ENV": "production"})

    weak_overrides = (
        {"PILOT_GITHUB_REPOSITORY": "PetrFedin/other"},
        {"PILOT_GITHUB_PROTECTED_BRANCH": "pilot/e2e-hardening-20260808"},
        {"PILOT_GITHUB_REQUIRED_CHECKS": "backend,frontend"},
        {"PILOT_GITHUB_ACTIONS_APP_ID": "1"},
        {"PILOT_GITHUB_WORKFLOW_NAME": "Weak CI"},
        {"PILOT_GITHUB_WORKFLOW_PATH": "weak.yml"},
    )
    for overrides in weak_overrides:
        with pytest.raises(ValueError, match="trust anchor mismatch"):
            require_trusted_configuration(_env(**overrides))


def test_trusted_workflow_candidates_accept_only_exact_push_run():
    runs = [
        _workflow_run(id=1, event="pull_request", updated_at="2026-08-19T10:00:00Z"),
        _workflow_run(id=2, path=".github/workflows/weak.yml"),
        _workflow_run(id=3, head_sha="b" * 40),
        _workflow_run(id=4, conclusion="failure"),
        _workflow_run(id=5, updated_at="2026-08-19T11:00:00Z"),
        _workflow_run(id=6, updated_at="2026-08-19T10:30:00Z"),
    ]

    candidates = trusted_workflow_candidates(runs, release_commit="a" * 40)

    assert [item["id"] for item in candidates] == [5, 6]


def test_trusted_workflow_jobs_require_all_six_successes():
    jobs = _jobs()
    assert trusted_workflow_job_errors(jobs) == []
    assert trusted_workflow_job_evidence(jobs) == _required_jobs()

    missing = jobs[:-1]
    assert trusted_workflow_job_errors(missing) == [
        "GitHub trusted workflow job is missing: docker"
    ]
    with pytest.raises(ValueError, match="docker"):
        trusted_workflow_job_evidence(missing)

    failed = [dict(item) for item in jobs]
    failed[0]["conclusion"] = "failure"
    assert trusted_workflow_job_errors(failed) == [
        "GitHub trusted workflow job is not successful: backend"
    ]


def test_report_trust_anchor_requires_signed_exact_six_job_verdicts():
    assert report_trust_anchor_errors(_report()) == []

    missing = _report(required_jobs=None)
    errors = report_trust_anchor_errors(missing)
    assert any("workflow job evidence is missing" in error for error in errors)

    incomplete = _report(required_jobs={name: "success" for name in TRUSTED_REQUIRED_CHECKS[:-1]})
    errors = report_trust_anchor_errors(incomplete)
    assert any("workflow jobs do not match" in error for error in errors)
    assert any("docker" in error for error in errors)

    failed = _report(required_jobs={**_required_jobs(), "backend": "failure"})
    errors = report_trust_anchor_errors(failed)
    assert any("workflow job is not successful: backend" in error for error in errors)


def test_report_trust_anchor_rejects_pull_request_run_and_wrong_source():
    assert report_trust_anchor_errors(_report()) == []

    pr_errors = report_trust_anchor_errors(_report(event="pull_request"))
    assert "repository governance workflow must be an exact protected-main push run" in pr_errors

    wrong_app = _report()
    wrong_app["policy"]["actions_app_id"] = 1
    wrong_app["policy"]["observed_check_sources"]["backend"] = [1]
    errors = report_trust_anchor_errors(wrong_app)
    assert any("trusted GitHub Actions app" in error for error in errors)
    assert any("trusted check source is invalid: backend" in error for error in errors)


def test_report_trust_anchor_rejects_mirrored_repo_or_non_main_branch():
    mirrored = _report()
    mirrored["repository"]["full_name"] = "PetrFedin/flashin-mirror"
    errors = report_trust_anchor_errors(mirrored)
    assert any("trusted FLASHIN repository" in error for error in errors)

    non_main = _report()
    non_main["branch"]["name"] = "pilot/e2e-hardening-20260808"
    errors = report_trust_anchor_errors(non_main)
    assert any("trusted main branch" in error for error in errors)
