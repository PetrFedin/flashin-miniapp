import json
from datetime import UTC, datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from pilot_evidence import configuration_fingerprint, sha256_file  # noqa: E402
from pilot_governance_admission import validate_attached_governance  # noqa: E402
from pilot_repository_governance import build_report  # noqa: E402

NOW = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
SECRET = "pilot-governance-trust-anchor-secret-0123456789"
RELEASE = {
    "release_id": "release-governance-anchor",
    "git_commit": "a" * 40,
    "sha256": "b" * 64,
    "promoted_at": "2026-08-19T09:00:00Z",
}
REQUIRED_CHECKS = (
    "backend",
    "frontend",
    "admin",
    "browser-e2e",
    "integrated-e2e",
    "docker",
)


def _weak_env():
    return {
        "APP_ENV": "production",
        "API_PUBLIC_URL": "https://api.flashin.example",
        "MINI_APP_URL": "https://mini.flashin.example",
        "ADMIN_URL": "https://admin.flashin.example",
        "PILOT_EVIDENCE_SIGNING_SECRET": SECRET,
        "PILOT_GITHUB_REPOSITORY": "PetrFedin/flashin-miniapp",
        "PILOT_GITHUB_PROTECTED_BRANCH": "main",
        "PILOT_GITHUB_REQUIRED_CHECKS": ",".join(REQUIRED_CHECKS),
        "PILOT_GITHUB_WORKFLOW_NAME": "CI",
        "PILOT_GITHUB_WORKFLOW_PATH": "ci.yml",
        "PILOT_GITHUB_ACTIONS_APP_ID": "1",
        "PILOT_GITHUB_GOVERNANCE_MAX_AGE_MINUTES": "60",
    }


def _weak_snapshot():
    app_id = 1
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
                        {"context": name, "integration_id": app_id}
                        for name in REQUIRED_CHECKS
                    ],
                },
            },
            {"type": "non_fast_forward", "ruleset_id": 41},
            {"type": "deletion", "ruleset_id": 41},
        ],
        "rulesets": [
            {
                "id": 41,
                "name": "weak-source-fixture",
                "enforcement": "active",
                "bypass_actors": [],
            }
        ],
        "workflow_runs": [
            {
                "id": 1001,
                "name": "CI",
                "path": ".github/workflows/ci.yml",
                "event": "push",
                "status": "completed",
                "conclusion": "success",
                "head_sha": RELEASE["git_commit"],
                "html_url": "https://github.com/PetrFedin/flashin-miniapp/actions/runs/1001",
                "created_at": "2026-08-19T09:30:00Z",
                "updated_at": "2026-08-19T09:45:00Z",
            }
        ],
    }


def test_admission_rejects_signed_governance_report_using_weakened_env_anchor(tmp_path):
    env = _weak_env()
    report = build_report(
        _weak_snapshot(),
        env=env,
        current_release=RELEASE,
        owner="Technical",
        now=NOW,
    )
    assert report["policy"]["actions_app_id"] == 1

    pilot_dir = tmp_path / "docs/pilot"
    pilot_dir.mkdir(parents=True)
    report_path = pilot_dir / "repository_governance_report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    manifest = {
        "release": RELEASE,
        "approvals": {"technical_owner": "Technical"},
        "acknowledgements": {"repository_governance_verified": True},
        "configuration_fingerprint": configuration_fingerprint(env, SECRET),
        "evidence": {
            "repository_governance_report": {
                "path": "docs/pilot/repository_governance_report.json",
                "sha256": sha256_file(report_path),
            }
        },
    }
    manifest_path = pilot_dir / "pilot_admission_manifest.json"

    errors = validate_attached_governance(
        manifest_path,
        manifest,
        env=env,
        root=tmp_path,
        max_age_minutes=60,
        now=NOW,
    )

    assert any("trusted GitHub Actions app" in error for error in errors)
    assert any("trusted check source is invalid" in error for error in errors)
