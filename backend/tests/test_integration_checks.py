import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from check_integrations import (  # noqa: E402
    PROBES,
    build_probe_plan,
    build_report,
    redact,
    run_probe,
)


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
    env = {
        "TELEGRAM_BOT_TOKEN": "telegram-secret-123",
        "YOOKASSA_SECRET_KEY": "yookassa-secret-456",
    }
    output = redact("telegram-secret-123 / yookassa-secret-456", env)
    assert "telegram-secret-123" not in output
    assert "yookassa-secret-456" not in output
    assert output.count("<redacted>") == 2


def test_strict_report_is_fail_closed_and_advisory_is_not():
    results = [
        {"name": "telegram", "ok": True, "returncode": 0},
        {"name": "yookassa", "ok": False, "returncode": 1},
    ]
    strict = build_report(results, strict=True, host_python=False)
    advisory = build_report(results, strict=False, host_python=False)
    assert strict["go"] is False
    assert strict["summary"]["failed"] == 1
    assert advisory["go"] is True


def test_run_probe_uses_backend_container_and_redacts_output():
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
        env={"TELEGRAM_BOT_TOKEN": "telegram-secret-123"},
        host_python=False,
        runner=fake_runner,
    )
    assert captured["command"][:5] == ["docker", "compose", "exec", "-T", "backend"]
    assert result["ok"]
    assert "telegram-secret-123" not in result["stdout"]


def test_host_probe_receives_values_from_dotenv():
    captured = {}

    def fake_runner(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    run_probe(
        PROBES[0],
        env={"TELEGRAM_BOT_TOKEN": "token-from-dotenv"},
        host_python=True,
        runner=fake_runner,
    )
    assert captured["command"][0] == sys.executable
    assert captured["env"]["TELEGRAM_BOT_TOKEN"] == "token-from-dotenv"
