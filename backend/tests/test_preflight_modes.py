import base64
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = ROOT / "scripts" / "preflight.py"
SECRET_HELPER = ROOT / "scripts" / "ensure_webhook_secret.py"
LAUNCHER = ROOT / "scripts" / "launch.py"
REQUIRED_ENV = {
    "DATABASE_URL": "sqlite://",
    "TELEGRAM_BOT_TOKEN": "test-bot-token",
    "TELEGRAM_WEBHOOK_SECRET": "test-webhook-secret",
    "JWT_SECRET": "test-jwt-secret",
    "ADMIN_EMAIL": "admin@test.local",
    "ADMIN_PASSWORD": "test-password",
    "MINI_APP_URL": "http://localhost:5173",
    "API_PUBLIC_URL": "http://localhost:8000",
}


def load_secret_helper():
    spec = importlib.util.spec_from_file_location("ensure_webhook_secret", SECRET_HELPER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.ensure_webhook_secret


def load_launcher():
    spec = importlib.util.spec_from_file_location("flashin_launch", LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def read_webhook_secret(env_path):
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("TELEGRAM_WEBHOOK_SECRET="):
            return line.split("=", 1)[1]
    return None


def run_preflight(*arguments):
    return subprocess.run(
        [sys.executable, str(PREFLIGHT), *map(str, arguments)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def write_env(path: Path, **overrides):
    values = {**REQUIRED_ENV, **overrides}
    path.write_text(
        "".join(f"{key}={value}\n" for key, value in values.items()),
        encoding="utf-8",
    )


def test_source_only_preflight_does_not_require_env(tmp_path):
    missing_env = tmp_path / "missing.env"

    result = run_preflight("--source-only", "--env-file", missing_env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Preflight OK" in result.stdout


def test_require_env_preflight_rejects_missing_env(tmp_path):
    missing_env = tmp_path / "missing.env"

    result = run_preflight("--require-env", "--env-file", missing_env)

    assert result.returncode == 1
    assert "Missing environment file" in result.stdout


def test_require_env_preflight_rejects_placeholder_secret(tmp_path):
    env_path = tmp_path / "placeholder.env"
    write_env(
        env_path,
        TELEGRAM_WEBHOOK_SECRET="replace_with_random_webhook_secret",
    )

    result = run_preflight("--require-env", "--env-file", env_path)

    assert result.returncode == 1
    assert "must be a non-placeholder value" in result.stdout
    assert "replace_with_random_webhook_secret" not in result.stdout


def test_require_env_preflight_accepts_safe_temporary_env(tmp_path):
    env_path = tmp_path / "safe.env"
    write_env(env_path)

    result = run_preflight("--require-env", "--env-file", env_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Preflight OK" in result.stdout


def test_installers_generate_secret_and_preserve_real_value(tmp_path):
    ensure_webhook_secret = load_secret_helper()
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TELEGRAM_WEBHOOK_SECRET=replace_with_random_webhook_secret\n",
        encoding="utf-8",
    )

    assert ensure_webhook_secret(env_path) is True
    generated = env_path.read_text(encoding="utf-8").split("=", 1)[1].strip()
    padding = "=" * (-len(generated) % 4)
    assert len(base64.urlsafe_b64decode(generated + padding)) >= 32

    env_path.write_text(
        "TELEGRAM_WEBHOOK_SECRET=user-provided-secret\n",
        encoding="utf-8",
    )
    assert ensure_webhook_secret(env_path) is False
    assert env_path.read_text(encoding="utf-8") == (
        "TELEGRAM_WEBHOOK_SECRET=user-provided-secret\n"
    )

    for script_name in ("install.sh", "bootstrap.sh"):
        script = (ROOT / "scripts" / script_name).read_text(encoding="utf-8")
        generator_call = "python3 scripts/ensure_webhook_secret.py .env"
        strict_preflight = "python3 scripts/preflight.py --require-env"
        assert generator_call in script
        assert script.index(generator_call) < script.index(strict_preflight)


@pytest.mark.parametrize("mode", ["local", "production"])
def test_fresh_launch_env_gets_secret_before_preflight(mode, tmp_path, capsys):
    launcher = load_launcher()
    launcher.ENV = tmp_path / ".env"

    launcher.prepare_env(mode)

    generated = read_webhook_secret(launcher.ENV)
    captured = capsys.readouterr()
    assert generated
    assert generated not in {
        "change-me",
        "replace_with_random_webhook_secret",
    }
    assert generated not in captured.out
    assert generated not in captured.err


def test_launch_preserves_existing_real_webhook_secret(tmp_path, capsys):
    launcher = load_launcher()
    launcher.ENV = tmp_path / ".env"
    launcher.ENV.write_text(
        "TELEGRAM_WEBHOOK_SECRET=user-provided-launch-secret\n",
        encoding="utf-8",
    )

    launcher.prepare_env("local")

    captured = capsys.readouterr()
    assert read_webhook_secret(launcher.ENV) == "user-provided-launch-secret"
    assert "user-provided-launch-secret" not in captured.out
    assert "user-provided-launch-secret" not in captured.err


def test_launch_adds_missing_webhook_secret(tmp_path):
    launcher = load_launcher()
    launcher.ENV = tmp_path / ".env"
    launcher.ENV.write_text("JWT_SECRET=test-jwt-secret\n", encoding="utf-8")

    launcher.prepare_env("local")

    assert read_webhook_secret(launcher.ENV)


def test_launch_runs_strict_preflight_after_secret_generation(monkeypatch):
    launcher = load_launcher()
    events = []
    monkeypatch.setattr(
        launcher,
        "write_env_if_missing",
        lambda mode: events.append(("write_env", mode)),
    )
    monkeypatch.setattr(
        launcher,
        "ensure_launch_webhook_secret",
        lambda: events.append(("webhook_secret", None)),
    )
    monkeypatch.setattr(launcher, "validate_minimum_env", lambda: True)

    def record_run(command, required=True):
        events.append((command, required))
        return 0

    monkeypatch.setattr(launcher, "run", record_run)
    monkeypatch.setattr(sys, "argv", [str(LAUNCHER), "--mode", "local", "--skip-build"])

    launcher.main()

    secret_index = events.index(("webhook_secret", None))
    preflight_index = events.index(
        ("python3 scripts/preflight.py --require-env", True)
    )
    assert events[0] == ("write_env", "local")
    assert secret_index < preflight_index
