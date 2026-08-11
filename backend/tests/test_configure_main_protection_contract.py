from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import configure_main_protection as protection  # noqa: E402


EXPECTED_CHECKS = [
    "backend",
    "frontend",
    "admin",
    "browser-e2e",
    "integrated-e2e",
    "docker",
]


def _github_response(*, strict=True, admins=True, force=False, deletions=False, conversations=True):
    return {
        "required_status_checks": {
            "strict": strict,
            "checks": [
                {
                    "context": name,
                    "app_id": protection.DEFAULT_ACTIONS_APP_ID,
                }
                for name in EXPECTED_CHECKS
            ],
        },
        "enforce_admins": {"enabled": admins},
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": True,
            "require_code_owner_reviews": False,
            "required_approving_review_count": 0,
            "require_last_push_approval": False,
        },
        "allow_force_pushes": {"enabled": force},
        "allow_deletions": {"enabled": deletions},
        "required_conversation_resolution": {"enabled": conversations},
    }


def test_default_policy_is_exactly_the_six_ci_jobs():
    assert list(protection.DEFAULT_CHECKS) == EXPECTED_CHECKS
    assert protection.DEFAULT_ACTIONS_APP_ID == 15368
    assert protection.API_ROOT == "https://api.github.com"


def test_target_cannot_relax_required_checks_or_actions_app():
    repository, branch, checks, app_id = protection.load_target({})
    assert repository == "PetrFedin/flashin-miniapp"
    assert branch == "main"
    assert checks == EXPECTED_CHECKS
    assert app_id == protection.DEFAULT_ACTIONS_APP_ID

    with pytest.raises(ValueError, match="must exactly equal"):
        protection.load_target(
            {"PILOT_GITHUB_REQUIRED_CHECKS": "backend,frontend,admin"}
        )

    with pytest.raises(ValueError, match="must be 15368"):
        protection.load_target({"PILOT_GITHUB_ACTIONS_APP_ID": "1"})


def test_payload_is_strict_pr_only_admin_enforced_and_non_destructive():
    payload = protection.build_protection_payload(
        EXPECTED_CHECKS,
        protection.DEFAULT_ACTIONS_APP_ID,
    )

    status = payload["required_status_checks"]
    assert status["strict"] is True
    assert status["contexts"] == []
    assert status["checks"] == [
        {"context": name, "app_id": protection.DEFAULT_ACTIONS_APP_ID}
        for name in EXPECTED_CHECKS
    ]
    assert payload["enforce_admins"] is True
    assert payload["required_pull_request_reviews"] == {
        "dismiss_stale_reviews": True,
        "require_code_owner_reviews": False,
        "required_approving_review_count": 0,
        "require_last_push_approval": False,
    }
    assert payload["allow_force_pushes"] is False
    assert payload["allow_deletions"] is False
    assert payload["required_conversation_resolution"] is True


def test_github_response_only_goes_green_for_the_exact_policy():
    good = protection.summarize_protection(
        _github_response(),
        EXPECTED_CHECKS,
        protection.DEFAULT_ACTIONS_APP_ID,
    )
    assert good["go"] is True

    for weak_response in (
        _github_response(strict=False),
        _github_response(admins=False),
        _github_response(force=True),
        _github_response(deletions=True),
        _github_response(conversations=False),
    ):
        summary = protection.summarize_protection(
            weak_response,
            EXPECTED_CHECKS,
            protection.DEFAULT_ACTIONS_APP_ID,
        )
        assert summary["go"] is False


def test_missing_or_wrong_required_check_fails_closed():
    missing = _github_response()
    missing["required_status_checks"]["checks"] = missing["required_status_checks"]["checks"][:-1]
    assert protection.summarize_protection(
        missing,
        EXPECTED_CHECKS,
        protection.DEFAULT_ACTIONS_APP_ID,
    )["go"] is False

    wrong_app = _github_response()
    wrong_app["required_status_checks"]["checks"][0]["app_id"] = 1
    assert protection.summarize_protection(
        wrong_app,
        EXPECTED_CHECKS,
        protection.DEFAULT_ACTIONS_APP_ID,
    )["go"] is False
