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


def _report(**workflow_overrides):
    workflow = {
        "id": 1001,
        "name": TRUSTED_WORKFLOW_NAME,
        "path": TRUSTED_WORKFLOW_API_PATH,
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "head_sha": "a" * 40,
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


def test_trusted_governance_configuration_accepts_only_exact_policy():
    require_trusted_configuration(_env())

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
