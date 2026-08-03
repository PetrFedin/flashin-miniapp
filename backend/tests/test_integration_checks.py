import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import check_integrations  # noqa: E402
from check_integrations import (  # noqa: E402
    PROBES,
    build_probe_context,
    build_probe_plan,
    build_report,
    redact,
    run_probe,
)
from check_yookassa_test import build_idempotence_key, validate_response  # noqa: E402
from pilot_evidence import validate_provider_report  # noqa: E402


def production_env() -> dict[str, str]:
    return {
        "PILOT_EVIDENCE_SIGNING_SECRET": "s" * 48,
        "APP_ENV": "production",
        "TELEGRAM_BOT_TOKEN": "telegram-secret-123",
        "YOOKASSA_SHOP_ID": "shop-1",
        "YOOKASSA_SECRET_KEY": "yookassa-secret-456",
        "YOOKASSA_RETURN_URL": "https://mini.flashin.store/payment-result",
        "MOYSKLAD_TOKEN": "moy-token",
        "MEDIA_STORAGE": "r2",
        "S3_BUCKET": "bucket",
        "S3_ACCESS_KEY_ID": "access",
        "S3_SECRET_ACCESS_KEY": "secret2",
        "MEILISEARCH_ENABLED": "true",
        "MEILISEARCH_MASTER_KEY": "meili",
    }


def current_release() -> dict[str, str]:
    return {
        "release_id": "release-1",
        "git_commit": "a" * 40,
        "sha256": "b" * 64,
        "promoted_at": "2026-08-03T17:00:00Z",
    }


def test_probe_plan_requires_durable_media_and_search():
    plan = build_probe_plan({"MEDIA_STORAGE": "local", "MEILISEARCH_ENABLED": "false"})
    by_name = {item["probe"].name: item for item in plan}
    assert by_name["telegram"]["enabled"]
    assert by_name["yookassa"]["enabled"]
    assert by_name["moysklad"]["enabled"]
    assert not by_name["r2_s3"]["enabled"]
    assert not by_name["meilisearch"]["enabled"]

    production = build_probe_plan({"MEDIA_STORAGE": "r2", "MEILISEARCH_ENABLED": "true"})
    assert all(item["enabled"] for item in production)


def test_redaction_removes_all_known_secrets():
    env = production_env()
    output = redact("telegram-secret-123 / yookassa-secret-456", env)
    assert "telegram-secret-123" not in output
    assert "yookassa-secret-456" not in output
    assert output.count("<redacted>") == 2


def test_run_probe_uses_backend_container_context_and_redacts_output():
    captured = {}

    def fake_runner(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="token=telegram-secret-123",
            stderr="",
        )

    result = run_probe(
        PROBES[0],
        env=production_env(),
        host_python=False,
        probe_context={"FLASHIN_PROBE_RUN_ID": "run-1"},
        runner=fake_runner,
    )
    assert captured["command"][:4] == ["docker", "compose", "exec", "-T"]
    assert "FLASHIN_PROBE_RUN_ID=run-1" in captured["command"]
    assert result["ok"]
    assert "telegram-secret-123" not in result["stdout"]


def test_host_probe_receives_dotenv_and_probe_context():
    captured = {}

    def fake_runner(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    run_probe(
        PROBES[0],
        env=production_env(),
        host_python=True,
        probe_context={"FLASHIN_PROBE_RUN_ID": "run-1"},
        runner=fake_runner,
    )
    assert captured["command"][0] == sys.executable
    assert captured["env"]["TELEGRAM_BOT_TOKEN"] == "telegram-secret-123"
    assert captured["env"]["FLASHIN_PROBE_RUN_ID"] == "run-1"


def test_probe_context_and_yookassa_idempotence_are_stable_per_payload():
    first = build_probe_context(production_env(), current_release(), "run-1")
    second = build_probe_context(production_env(), current_release(), "run-2")
    assert first["FLASHIN_YOOKASSA_IDEMPOTENCE_KEY"] == second["FLASHIN_YOOKASSA_IDEMPOTENCE_KEY"]
    assert first["FLASHIN_PROBE_RUN_ID"] != second["FLASHIN_PROBE_RUN_ID"]
    assert build_idempotence_key(
        "shop-1", "a" * 40, "https://mini.flashin.store/payment-result"
    ) == first["FLASHIN_YOOKASSA_IDEMPOTENCE_KEY"]
    changed = production_env()
    changed["YOOKASSA_RETURN_URL"] = "https://mini.flashin.store/new-result"
    third = build_probe_context(changed, current_release(), "run-3")
    assert third["FLASHIN_YOOKASSA_IDEMPOTENCE_KEY"] != first["FLASHIN_YOOKASSA_IDEMPOTENCE_KEY"]


def test_signed_strict_report_is_accepted_by_verifier():
    env = production_env()
    release = current_release()
    results = [
        {"name": item.name, "ok": True, "returncode": 0, "stdout": "ok", "stderr": ""}
        for item in PROBES
    ]
    now = datetime(2026, 8, 3, 18, 0, tzinfo=UTC)
    report = build_report(
        results,
        strict=True,
        host_python=False,
        env=env,
        current_release=release,
        max_age_minutes=60,
        run_id="run-1",
        created_at=now,
    )
    assert not validate_provider_report(
        report,
        env=env,
        current_release=release,
        now=now,
        max_age_minutes=60,
    )


def test_default_cli_path_verifies_existing_evidence_without_running_probes(monkeypatch):
    valid_report = {"go": True, "created_at": "now", "expires_at": "later", "summary": {}}
    monkeypatch.setattr(check_integrations, "read_env", lambda _path: production_env())
    monkeypatch.setattr(check_integrations, "load_current_release", lambda _root: current_release())
    monkeypatch.setattr(
        check_integrations,
        "verify_existing_report",
        lambda *_args, **_kwargs: (valid_report, []),
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("default verification must not execute provider probes")

    monkeypatch.setattr(check_integrations, "run_probe", forbidden)
    assert check_integrations.main([]) == 0


def test_yookassa_response_validation_checks_amount_currency_and_confirmation():
    valid = {
        "id": "payment-1",
        "status": "pending",
        "amount": {"value": "1.00", "currency": "RUB"},
        "confirmation": {"confirmation_url": "https://example.invalid/confirm"},
    }
    assert validate_response(valid) == []
    invalid = dict(valid)
    invalid["amount"] = {"value": "2.00", "currency": "USD"}
    errors = validate_response(invalid)
    assert any("amount mismatch" in item for item in errors)
    assert any("currency mismatch" in item for item in errors)


def test_live_evidence_and_admission_reports_are_gitignored():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for path in (
        "docs/pilot/integration_check_report.json",
        "docs/pilot/integration_check_report.md",
        "docs/pilot/rollback_drill_report.json",
        "docs/pilot/rollback_drill_report.md",
        "docs/pilot/pilot_admission_manifest.json",
        "docs/pilot/pilot_admission_manifest.md",
        "docs/readiness_gate_report.json",
        "docs/readiness_gate_report.md",
        "docs/pilot_live_gate_report.json",
        "docs/pilot_live_gate_report.md",
    ):
        assert path in ignored
