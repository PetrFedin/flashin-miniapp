from datetime import UTC, datetime
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot_repository_governance import (  # noqa: E402
    _github_token,
    build_report,
    evaluate_snapshot,
    settings,
)

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
RELEASE = {
    "release_id": "release-governance",
    "git_commit": "a" * 40,
    "sha256": "b" * 64,
    "promoted_at": "2026-08-06T11:00:00Z",
}
REQUIRED_CHECKS = ("backend", "frontend", "admin", "browser-e2e", "docker")


def _env():
    return {
        "APP_ENV": "production",
        "PILOT_EVIDENCE_SIGNING_SECRET": "pilot-governance-secret-0123456789abcdef",
        "PILOT_GITHUB_REPOSITORY": "PetrFedin/flashin-miniapp",
        "PILOT_GITHUB_PROTECTED_BRANCH": "main",
        "PILOT_GITHUB_REQUIRED_CHECKS": ",".join(REQUIRED_CHECKS),
        "PILOT_GITHUB_WORKFLOW_NAME": "CI",
        "PILOT_GITHUB_WORKFLOW_PATH": "ci.yml",
        "PILOT_GITHUB_GOVERNANCE_MAX_AGE_MINUTES": "60",
    }


def _snapshot_without_visible_bypass_actors():
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
        "protection": None,
        "active_rules": [
            {"type": "pull_request", "ruleset_id": 41},
            {
                "type": "required_status_checks",
                "ruleset_id": 41,
                "parameters": {
                    "strict_required_status_checks_policy": True,
                    "required_status_checks": [
                        {"context": name, "integration_id": 15368}
                        for name in REQUIRED_CHECKS
                    ],
                },
            },
            {"type": "non_fast_forward", "ruleset_id": 41},
            {"type": "deletion", "ruleset_id": 41},
        ],
        # GitHub omits bypass_actors when the caller cannot see them.
        "rulesets": [{"id": 41, "name": "FLASHIN pilot main", "enforcement": "active"}],
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


def test_governance_collection_requires_github_token():
    with pytest.raises(ValueError, match="PILOT_GITHUB_TOKEN is required"):
        _github_token(_env())


def test_hidden_ruleset_bypass_data_fails_closed():
    env = _env()
    snapshot = _snapshot_without_visible_bypass_actors()

    normalized, errors = evaluate_snapshot(
        snapshot,
        config=settings(env),
        current_release=RELEASE,
    )

    assert normalized["policy"]["ruleset_bypass_visibility"] is False
    assert normalized["policy"]["administrator_bypass_blocked"] is False
    assert any("bypass actors are not visible" in error for error in errors)
    with pytest.raises(ValueError, match="Repository governance is not GO"):
        build_report(
            snapshot,
            env=env,
            current_release=RELEASE,
            owner="Technical",
            now=NOW,
        )
