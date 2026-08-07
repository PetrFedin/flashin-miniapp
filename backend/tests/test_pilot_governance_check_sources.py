from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot_repository_governance import evaluate_snapshot, settings  # noqa: E402

RELEASE = {
    "release_id": "release-governance",
    "git_commit": "a" * 40,
    "sha256": "b" * 64,
    "promoted_at": "2026-08-06T11:00:00Z",
}
REQUIRED_CHECKS = ("backend", "frontend", "admin", "browser-e2e", "docker")
ACTIONS_APP_ID = 15368


def _env():
    return {
        "PILOT_GITHUB_REPOSITORY": "PetrFedin/flashin-miniapp",
        "PILOT_GITHUB_PROTECTED_BRANCH": "main",
        "PILOT_GITHUB_REQUIRED_CHECKS": ",".join(REQUIRED_CHECKS),
        "PILOT_GITHUB_WORKFLOW_NAME": "CI",
        "PILOT_GITHUB_WORKFLOW_PATH": "ci.yml",
        "PILOT_GITHUB_ACTIONS_APP_ID": str(ACTIONS_APP_ID),
        "PILOT_GITHUB_GOVERNANCE_MAX_AGE_MINUTES": "60",
    }


def _workflow_runs():
    return [
        {
            "id": 677,
            "name": "CI",
            "path": ".github/workflows/ci.yml",
            "event": "push",
            "status": "completed",
            "conclusion": "success",
            "head_sha": RELEASE["git_commit"],
            "created_at": "2026-08-06T11:30:00Z",
            "updated_at": "2026-08-06T11:50:00Z",
        }
    ]


def _repository_and_branch():
    return {
        "repository": {
            "full_name": "PetrFedin/flashin-miniapp",
            "default_branch": "main",
            "archived": False,
        },
        "branch": {
            "name": "main",
            "protected": True,
            "commit": {"sha": RELEASE["git_commit"]},
        },
        "workflow_runs": _workflow_runs(),
    }


def test_ruleset_status_names_from_wrong_integration_fail_closed():
    snapshot = {
        **_repository_and_branch(),
        "protection": None,
        "active_rules": [
            {"type": "pull_request", "ruleset_id": 41},
            {
                "type": "required_status_checks",
                "ruleset_id": 41,
                "parameters": {
                    "strict_required_status_checks_policy": True,
                    "required_status_checks": [
                        {"context": name, "integration_id": 99999}
                        for name in REQUIRED_CHECKS
                    ],
                },
            },
            {"type": "non_fast_forward", "ruleset_id": 41},
            {"type": "deletion", "ruleset_id": 41},
        ],
        "rulesets": [{"id": 41, "bypass_actors": []}],
    }

    normalized, errors = evaluate_snapshot(
        snapshot,
        config=settings(_env()),
        current_release=RELEASE,
    )

    assert normalized["policy"]["required_status_checks"] is True
    assert normalized["policy"]["required_status_check_sources"] is False
    assert all(
        normalized["policy"]["observed_check_sources"][name] == [99999]
        for name in REQUIRED_CHECKS
    )
    assert any("not bound to the configured Actions app" in error for error in errors)


def test_classic_status_contexts_without_actions_app_binding_fail_closed():
    snapshot = {
        **_repository_and_branch(),
        "active_rules": [],
        "rulesets": [],
        "protection": {
            "required_status_checks": {
                "strict": True,
                "contexts": list(REQUIRED_CHECKS),
                "checks": [
                    {"context": name, "app_id": -1}
                    for name in REQUIRED_CHECKS
                ],
            },
            "required_pull_request_reviews": {"required_approving_review_count": 1},
            "enforce_admins": {"enabled": True},
            "allow_force_pushes": {"enabled": False},
            "allow_deletions": {"enabled": False},
        },
    }

    normalized, errors = evaluate_snapshot(
        snapshot,
        config=settings(_env()),
        current_release=RELEASE,
    )

    assert normalized["policy"]["required_status_checks"] is True
    assert normalized["policy"]["required_status_check_sources"] is False
    assert any("required_status_check_sources" in error for error in errors)
    assert any("not bound to the configured Actions app" in error for error in errors)
