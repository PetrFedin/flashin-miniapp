from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import pilot_governance_operator as operator  # noqa: E402
from pilot_admission_path import verify_admission_path  # noqa: E402
from pilot_control_binding import build_admission_binding  # noqa: E402
from pilot_operator_security import (  # noqa: E402
    forbidden_application_env_keys,
    validate_application_token_isolation,
)


def test_application_env_rejects_privileged_token_keys_even_when_empty(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "APP_ENV=production\nPILOT_GITHUB_TOKEN=\nGITHUB_TOKEN=accidental\n",
        encoding="utf-8",
    )

    assert forbidden_application_env_keys(env_path) == [
        "PILOT_GITHUB_TOKEN",
        "GITHUB_TOKEN",
    ]
    errors = validate_application_token_isolation(tmp_path, {})
    assert any("must not be stored in application .env" in error for error in errors)


def test_operator_normalizes_omitted_governance_settings_to_trusted_defaults():
    normalized = operator._trusted_env({"APP_ENV": "production"})

    assert normalized["PILOT_GITHUB_REPOSITORY"] == operator.TRUSTED_REPOSITORY
    assert normalized["PILOT_GITHUB_PROTECTED_BRANCH"] == operator.TRUSTED_BRANCH
    assert normalized["PILOT_GITHUB_REQUIRED_CHECKS"] == ",".join(
        operator.TRUSTED_REQUIRED_CHECKS
    )
    assert normalized["PILOT_GITHUB_ACTIONS_APP_ID"] == str(
        operator.TRUSTED_ACTIONS_APP_ID
    )
    assert normalized["PILOT_GITHUB_WORKFLOW_NAME"] == operator.TRUSTED_WORKFLOW_NAME
    assert (
        normalized["PILOT_GITHUB_WORKFLOW_PATH"]
        == operator.TRUSTED_WORKFLOW_CONFIG_PATH
    )


def test_operator_binds_exact_six_job_verdicts_before_resigning(monkeypatch):
    report = {
        "workflow": {"id": 1001, "name": "CI"},
        "signature": "old-signature",
    }
    jobs = [
        {"name": name, "status": "completed", "conclusion": "success"}
        for name in operator.TRUSTED_REQUIRED_CHECKS
    ]
    seen = {}

    monkeypatch.setattr(
        operator.pilot_repository_governance,
        "require_signing_secret",
        lambda _env: "trusted-secret",
    )

    def fake_sign(payload, secret):
        seen["secret"] = secret
        seen["payload"] = payload
        return {**payload, "signature": "new-signature"}

    monkeypatch.setattr(operator.pilot_repository_governance, "sign_payload", fake_sign)

    bound = operator._bind_trusted_job_evidence(
        report,
        jobs=jobs,
        env={"PILOT_EVIDENCE_SIGNING_SECRET": "ignored-by-fake"},
    )

    assert seen["secret"] == "trusted-secret"
    assert seen["payload"]["workflow"]["required_jobs"] == {
        name: "success" for name in operator.TRUSTED_REQUIRED_CHECKS
    }
    assert "signature" not in seen["payload"]
    assert bound["signature"] == "new-signature"


def test_operator_binds_exact_security_verdicts_before_resigning(monkeypatch):
    report = {
        "workflow": {
            "id": 1001,
            "name": "CI",
            "required_jobs": {
                name: "success" for name in operator.TRUSTED_REQUIRED_CHECKS
            },
        },
        "signature": "old-signature",
    }
    workflow_run = {
        "id": 2001,
        "name": operator.TRUSTED_SECURITY_WORKFLOW_NAME,
        "path": operator.TRUSTED_SECURITY_WORKFLOW_API_PATH,
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "head_sha": "a" * 40,
        "html_url": "https://github.com/example/security/2001",
        "created_at": "2026-08-20T10:00:00Z",
        "updated_at": "2026-08-20T10:30:00Z",
    }
    jobs = [
        {"name": name, "status": "completed", "conclusion": "success"}
        for name in operator.TRUSTED_SECURITY_REQUIRED_JOBS
    ]
    seen = {}

    monkeypatch.setattr(
        operator.pilot_repository_governance,
        "require_signing_secret",
        lambda _env: "trusted-secret",
    )

    def fake_sign(payload, secret):
        seen["secret"] = secret
        seen["payload"] = payload
        return {**payload, "signature": "new-signature"}

    monkeypatch.setattr(operator.pilot_repository_governance, "sign_payload", fake_sign)

    bound = operator._bind_trusted_security_evidence(
        report,
        workflow_run=workflow_run,
        jobs=jobs,
        env={"PILOT_EVIDENCE_SIGNING_SECRET": "ignored-by-fake"},
    )

    assert seen["secret"] == "trusted-secret"
    assert seen["payload"]["security_workflow"]["required_jobs"] == {
        name: "success" for name in operator.TRUSTED_SECURITY_REQUIRED_JOBS
    }
    assert seen["payload"]["security_workflow"]["head_sha"] == "a" * 40
    assert "signature" not in seen["payload"]
    assert bound["signature"] == "new-signature"


def test_operator_entrypoint_allows_process_token_only_when_file_is_clean(
    tmp_path, monkeypatch
):
    (tmp_path / ".env").write_text("APP_ENV=production\n", encoding="utf-8")
    seen = []

    monkeypatch.setattr(operator, "ROOT", tmp_path)
    monkeypatch.setattr(
        operator,
        "_verify_report",
        lambda report_path, *, env: seen.append((report_path, env)) or 17,
    )
    monkeypatch.setenv("PILOT_GITHUB_TOKEN", "operator-only-token")

    assert operator.main(["verify"]) == 17
    assert len(seen) == 1
    assert seen[0][1]["PILOT_GITHUB_TOKEN"] == "operator-only-token"

    (tmp_path / ".env").write_text(
        "APP_ENV=production\nPILOT_GITHUB_TOKEN=forbidden\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must not be stored in application .env"):
        operator.main(["verify"])


def test_admission_and_runtime_reject_process_token_leakage(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("APP_ENV=production\n", encoding="utf-8")
    monkeypatch.setenv("PILOT_GITHUB_TOKEN", "leaked-operator-token")

    admission_errors = verify_admission_path(
        tmp_path / "docs/pilot/pilot_admission_manifest.json",
        tmp_path,
    )
    assert any("application process environment" in error for error in admission_errors)

    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    manifest = {
        "created_at": "2026-08-06T12:00:00Z",
        "configuration_fingerprint": "a" * 64,
        "release": {
            "release_id": "release",
            "git_commit": "b" * 40,
            "sha256": "c" * 64,
        },
    }
    with pytest.raises(ValueError, match="application process environment"):
        build_admission_binding(
            manifest_path,
            manifest,
            root=tmp_path,
            require_live_lifecycle=False,
            require_repository_governance=True,
        )


def test_repository_templates_and_compose_never_wire_privileged_token():
    production_env = (ROOT / ".env.production.example").read_text(encoding="utf-8")
    assignments = {
        line.split("=", 1)[0].strip()
        for line in production_env.splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
    }
    assert "PILOT_GITHUB_TOKEN" not in assignments
    assert "GITHUB_TOKEN" not in assignments

    compose = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in ("docker-compose.yml", "docker-compose.production.yml")
    )
    assert "PILOT_GITHUB_TOKEN" not in compose
    assert "GITHUB_TOKEN" not in compose
