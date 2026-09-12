import pytest

from scripts.configure_main_protection import (
    DEFAULT_ACTIONS_APP_ID,
    DEFAULT_CHECKS,
    build_protection_payload,
    load_target,
    summarize_protection,
)


def test_build_protection_payload_is_strict_and_app_bound():
    payload = build_protection_payload(DEFAULT_CHECKS, DEFAULT_ACTIONS_APP_ID)

    status = payload["required_status_checks"]
    assert status["strict"] is True
    assert status["contexts"] == []
    assert status["checks"] == [
        {"context": name, "app_id": DEFAULT_ACTIONS_APP_ID}
        for name in DEFAULT_CHECKS
    ]
    assert payload["enforce_admins"] is True
    assert payload["required_pull_request_reviews"]["required_approving_review_count"] == 0
    assert payload["allow_force_pushes"] is False
    assert payload["allow_deletions"] is False
    assert payload["required_conversation_resolution"] is True


def test_load_target_rejects_weaker_required_checks():
    env = {
        "PILOT_GITHUB_REPOSITORY": "PetrFedin/flashin-miniapp",
        "PILOT_GITHUB_PROTECTED_BRANCH": "main",
        "PILOT_GITHUB_REQUIRED_CHECKS": "backend,frontend",
        "PILOT_GITHUB_ACTIONS_APP_ID": str(DEFAULT_ACTIONS_APP_ID),
    }

    with pytest.raises(ValueError, match="exactly equal"):
        load_target(env)


def test_summarize_protection_requires_all_fail_closed_controls():
    response = {
        "required_status_checks": {
            "strict": True,
            "checks": [
                {"context": name, "app_id": DEFAULT_ACTIONS_APP_ID}
                for name in DEFAULT_CHECKS
            ],
        },
        "enforce_admins": {"enabled": True},
        "required_pull_request_reviews": {"required_approving_review_count": 0},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        "required_conversation_resolution": {"enabled": True},
    }

    summary = summarize_protection(response, DEFAULT_CHECKS, DEFAULT_ACTIONS_APP_ID)
    assert summary["go"] is True

    response["allow_force_pushes"] = {"enabled": True}
    summary = summarize_protection(response, DEFAULT_CHECKS, DEFAULT_ACTIONS_APP_ID)
    assert summary["go"] is False
