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


def _classic_snapshot():
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
        "active_rules": [],
        "rulesets": [],
        "protection": {
            "required_status_checks": {
                "strict": True,
                "contexts": list(REQUIRED_CHECKS),
                "checks": [
                    {"context": name, "app_id": ACTIONS_APP_ID}
                    for name in REQUIRED_CHECKS
                ],
            },
            "required_pull_request_reviews": {"required_approving_review_count": 1},
            "enforce_admins": {"enabled": True},
            "allow_force_pushes": {"enabled": False},
            "allow_deletions": {"enabled": False},
        },
        "workflow_runs": [
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
        ],
    }


def test_classic_protection_requires_explicit_force_push_and_deletion_flags():
    snapshot = _classic_snapshot()
    normalized, errors = evaluate_snapshot(
        snapshot,
        config=settings(_env()),
        current_release=RELEASE,
    )
    assert errors == []
    assert normalized["policy"]["force_push_blocked"] is True
    assert normalized["policy"]["deletion_blocked"] is True

    snapshot["protection"].pop("allow_force_pushes")
    snapshot["protection"].pop("allow_deletions")
    normalized, errors = evaluate_snapshot(
        snapshot,
        config=settings(_env()),
        current_release=RELEASE,
    )
    assert normalized["policy"]["force_push_blocked"] is False
    assert normalized["policy"]["deletion_blocked"] is False
    assert any("force_push_blocked" in error for error in errors)
    assert any("deletion_blocked" in error for error in errors)


def test_classic_protection_rejects_explicit_force_push_or_deletion_enablement():
    snapshot = _classic_snapshot()
    snapshot["protection"]["allow_force_pushes"]["enabled"] = True
    snapshot["protection"]["allow_deletions"]["enabled"] = True

    normalized, errors = evaluate_snapshot(
        snapshot,
        config=settings(_env()),
        current_release=RELEASE,
    )

    assert normalized["policy"]["force_push_blocked"] is False
    assert normalized["policy"]["deletion_blocked"] is False
    assert any("force_push_blocked" in error for error in errors)
    assert any("deletion_blocked" in error for error in errors)
