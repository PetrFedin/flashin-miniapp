import json
from datetime import UTC, datetime
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import pilot_evidence  # noqa: E402
import pilot_governance_release_guard  # noqa: E402
from pilot_control_binding import (  # noqa: E402
    REPOSITORY_GOVERNANCE_KEY,
    build_admission_binding,
)
from pilot_evidence import configuration_fingerprint, sha256_file  # noqa: E402
from pilot_governance_admission import validate_attached_governance  # noqa: E402
from pilot_repository_governance import (  # noqa: E402
    build_report,
    evaluate_snapshot,
    settings,
    validate_report,
)

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
SECRET = "pilot-governance-secret-0123456789abcdef"
RELEASE = {
    "release_id": "release-governance",
    "git_commit": "a" * 40,
    "sha256": "b" * 64,
    "promoted_at": "2026-08-06T11:00:00Z",
}
REQUIRED_CHECKS = ("backend", "frontend", "admin", "browser-e2e", "docker")
ACTIONS_APP_ID = 15368


def _env(**overrides):
    values = {
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
        "PILOT_GITHUB_ACTIONS_APP_ID": str(ACTIONS_APP_ID),
        "PILOT_GITHUB_GOVERNANCE_MAX_AGE_MINUTES": "60",
    }
    values.update(overrides)
    return values


def _write_env(root: Path, env):
    (root / ".env").write_text(
        "\n".join(f"{key}={value}" for key, value in env.items()) + "\n",
        encoding="utf-8",
    )


def _active_rules(checks=REQUIRED_CHECKS):
    return [
        {"type": "pull_request", "ruleset_id": 41},
        {
            "type": "required_status_checks",
            "ruleset_id": 41,
            "parameters": {
                "strict_required_status_checks_policy": True,
                "required_status_checks": [
                    {"context": name, "integration_id": ACTIONS_APP_ID}
                    for name in checks
                ],
            },
        },
        {"type": "non_fast_forward", "ruleset_id": 41},
        {"type": "deletion", "ruleset_id": 41},
    ]


def _snapshot(*, checks=REQUIRED_CHECKS, protected=True, bypass_actors=None):
    return {
        "repository": {
            "full_name": "PetrFedin/flashin-miniapp",
            "default_branch": "main",
            "archived": False,
        },
        "branch": {
            "name": "main",
            "protected": protected,
            "commit": {"sha": RELEASE["git_commit"]},
        },
        "protection": None,
        "active_rules": _active_rules(checks),
        "rulesets": [
            {
                "id": 41,
                "name": "FLASHIN pilot main",
                "enforcement": "active",
                "bypass_actors": bypass_actors or [],
            }
        ],
        "workflow_runs": [
            {
                "id": 677,
                "name": "CI",
                "path": ".github/workflows/ci.yml",
                "event": "push",
                "status": "completed",
                "conclusion": "success",
                "head_sha": RELEASE["git_commit"],
                "html_url": "https://github.com/PetrFedin/flashin-miniapp/actions/runs/677",
                "created_at": "2026-08-06T11:30:00Z",
                "updated_at": "2026-08-06T11:50:00Z",
            }
        ],
    }


def test_ruleset_governance_report_binds_exact_release_and_successful_ci():
    env = _env()
    report = build_report(
        _snapshot(),
        env=env,
        current_release=RELEASE,
        owner="Technical",
        now=NOW,
    )

    assert validate_report(
        report,
        env=env,
        expected_release=RELEASE,
        max_age_minutes=60,
        now=NOW,
    ) == []
    assert report["branch"]["head_sha"] == RELEASE["git_commit"]
    assert report["workflow"]["conclusion"] == "success"
    assert set(report["policy"]["required_checks"]) == set(REQUIRED_CHECKS)
    assert report["policy"]["actions_app_id"] == ACTIONS_APP_ID
    assert all(
        ACTIONS_APP_ID in report["policy"]["observed_check_sources"][name]
        for name in REQUIRED_CHECKS
    )

    tampered = json.loads(json.dumps(report))
    tampered["workflow"]["id"] = 999
    errors = validate_report(
        tampered,
        env=env,
        expected_release=RELEASE,
        max_age_minutes=60,
        now=NOW,
    )
    assert "repository governance evidence signature is invalid" in errors


def test_unprotected_branch_missing_checks_and_bypass_fail_closed():
    snapshot = _snapshot(
        checks=("backend", "frontend"),
        protected=False,
        bypass_actors=[
            {"actor_type": "RepositoryRole", "actor_id": 5, "bypass_mode": "always"}
        ],
    )
    normalized, errors = evaluate_snapshot(
        snapshot,
        config=settings(_env()),
        current_release=RELEASE,
    )

    assert normalized["policy"]["administrator_bypass_blocked"] is False
    assert any("required status checks are missing" in error for error in errors)
    assert any("administrator_bypass_blocked" in error for error in errors)
    with pytest.raises(ValueError, match="Repository governance is not GO"):
        build_report(
            snapshot,
            env=_env(),
            current_release=RELEASE,
            owner="Technical",
            now=NOW,
        )


def test_legacy_branch_protection_requires_admin_enforcement():
    snapshot = _snapshot()
    snapshot["active_rules"] = []
    snapshot["rulesets"] = []
    snapshot["protection"] = {
        "required_status_checks": {
            "strict": True,
            "contexts": list(REQUIRED_CHECKS),
            "checks": [
                {"context": name, "app_id": ACTIONS_APP_ID}
                for name in REQUIRED_CHECKS
            ],
        },
        "required_pull_request_reviews": {"required_approving_review_count": 1},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        "enforce_admins": {"enabled": True},
    }
    normalized, errors = evaluate_snapshot(
        snapshot,
        config=settings(_env()),
        current_release=RELEASE,
    )
    assert errors == []
    assert normalized["policy"]["administrator_bypass_blocked"] is True
    assert normalized["policy"]["required_status_check_sources"] is True

    snapshot["protection"]["enforce_admins"]["enabled"] = False
    _normalized, errors = evaluate_snapshot(
        snapshot,
        config=settings(_env()),
        current_release=RELEASE,
    )
    assert any("administrator_bypass_blocked" in error for error in errors)


def test_governance_attachment_requires_signed_technical_owner(tmp_path, monkeypatch):
    env = _env()
    _write_env(tmp_path, env)
    pilot_dir = tmp_path / "docs/pilot"
    pilot_dir.mkdir(parents=True)
    report = build_report(
        _snapshot(),
        env=env,
        current_release=RELEASE,
        owner="Technical",
        now=NOW,
    )
    report_path = pilot_dir / "repository_governance_report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "kind": "pilot_admission",
        "decision": "GO",
        "created_at": "2026-08-06T12:00:00Z",
        "configuration_fingerprint": configuration_fingerprint(env, SECRET),
        "release": RELEASE,
        "pilot_contract": {
            "maximum_orders": 20,
            "automatic_stop_on_critical_failure": True,
            "mass_admission_forbidden": True,
        },
        "approvals": {
            "business_owner": "Business",
            "operations_owner": "Operations",
            "technical_owner": "Technical",
            "legal_owner": "Legal",
            "support_owner": "Support",
        },
        "acknowledgements": {
            "live_lifecycle_completed": True,
            "repository_governance_verified": True,
        },
        "evidence": {
            "live_lifecycle_report": {"path": "fixture", "sha256": "c" * 64},
            "repository_governance_report": {
                "path": "docs/pilot/repository_governance_report.json",
                "sha256": sha256_file(report_path),
            },
        },
    }
    manifest_path = pilot_dir / "pilot_admission_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert validate_attached_governance(
        manifest_path,
        manifest,
        env=env,
        root=tmp_path,
        max_age_minutes=60,
        now=NOW,
    ) == []

    monkeypatch.setattr(
        pilot_governance_release_guard,
        "require_current_governance_release",
        lambda _root: RELEASE,
    )
    monkeypatch.setattr(pilot_evidence, "utc_now", lambda: NOW)
    binding = build_admission_binding(
        manifest_path,
        manifest,
        root=tmp_path,
        require_live_lifecycle=False,
        require_repository_governance=True,
    )
    assert binding[REPOSITORY_GOVERNANCE_KEY] == sha256_file(report_path)

    wrong_owner = build_report(
        _snapshot(),
        env=env,
        current_release=RELEASE,
        owner="Operations",
        now=NOW,
    )
    report_path.write_text(json.dumps(wrong_owner), encoding="utf-8")
    manifest["evidence"]["repository_governance_report"]["sha256"] = sha256_file(report_path)
    errors = validate_attached_governance(
        manifest_path,
        manifest,
        env=env,
        root=tmp_path,
        max_age_minutes=60,
        now=NOW,
    )
    assert "repository governance owner is not the signed technical owner" in errors
