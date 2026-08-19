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


def test_operator_entrypoint_allows_process_token_only_when_file_is_clean(
    tmp_path, monkeypatch
):
    (tmp_path / ".env").write_text("APP_ENV=production\n", encoding="utf-8")
    seen = []

    monkeypatch.setattr(operator, "ROOT", tmp_path)
    monkeypatch.setattr(
        operator.pilot_repository_governance,
        "main",
        lambda argv: seen.append(argv) or 17,
    )
    monkeypatch.setenv("PILOT_GITHUB_TOKEN", "operator-only-token")

    assert operator.main(["verify"]) == 17
    assert seen == [["verify"]]

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
