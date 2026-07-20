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
SCRIPTS_DIR = ROOT / "scripts"
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


def load_secret_helper_module():
    spec = importlib.util.spec_from_file_location("ensure_webhook_secret", SECRET_HELPER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_secret_helper():
    return load_secret_helper_module().ensure_webhook_secret


def load_preflight_module():
    spec = importlib.util.spec_from_file_location("flashin_preflight", PREFLIGHT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(SCRIPTS_DIR))
    return module


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


def write_env_with_webhook_assignment(path: Path, assignment: str):
    write_env(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    lines = [
        assignment if line.startswith("TELEGRAM_WEBHOOK_SECRET=") else line
        for line in lines
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


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


@pytest.mark.parametrize(
    "assignment",
    [
        "TELEGRAM_WEBHOOK_SECRET=replace_with_random_webhook_secret",
        'TELEGRAM_WEBHOOK_SECRET="replace_with_random_webhook_secret"',
        "TELEGRAM_WEBHOOK_SECRET='replace_with_random_webhook_secret'",
        'export TELEGRAM_WEBHOOK_SECRET = "replace_with_random_webhook_secret"',
        "TELEGRAM_WEBHOOK_SECRET=replace_with_random_webhook_secret # rotate",
        'TELEGRAM_WEBHOOK_SECRET="replace_with_random_webhook_secret" # rotate',
        "TELEGRAM_WEBHOOK_SECRET='replace_with_random_webhook_secret' # rotate",
        'export TELEGRAM_WEBHOOK_SECRET = "change-me" # rotate',
        'TELEGRAM_WEBHOOK_SECRET="" # rotate',
        "TELEGRAM_WEBHOOK_SECRET= # rotate",
    ],
)
def test_require_env_preflight_rejects_placeholder_secret(tmp_path, assignment):
    env_path = tmp_path / "placeholder.env"
    write_env_with_webhook_assignment(env_path, assignment)

    result = run_preflight("--require-env", "--env-file", env_path)

    assert result.returncode == 1
    assert "must be a non-placeholder value" in result.stdout
    assert "replace_with_random_webhook_secret" not in result.stdout


@pytest.mark.parametrize(
    "assignment",
    [
        'TELEGRAM_WEBHOOK_SECRET="quoted-real-secret"',
        "export TELEGRAM_WEBHOOK_SECRET = 'quoted-real-secret'",
    ],
)
def test_require_env_preflight_accepts_quoted_real_secret(tmp_path, assignment):
    env_path = tmp_path / "quoted-real.env"
    write_env_with_webhook_assignment(env_path, assignment)

    result = run_preflight("--require-env", "--env-file", env_path)

    assert result.returncode == 0, result.stdout + result.stderr


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


@pytest.mark.parametrize(
    "assignment",
    [
        "TELEGRAM_WEBHOOK_SECRET = real-secret",
        "export TELEGRAM_WEBHOOK_SECRET=real-secret",
        "TELEGRAM_WEBHOOK_SECRET='real-secret'",
        'export TELEGRAM_WEBHOOK_SECRET = "real-secret"',
        'TELEGRAM_WEBHOOK_SECRET="real#secret"',
        "TELEGRAM_WEBHOOK_SECRET='real#secret'",
        "TELEGRAM_WEBHOOK_SECRET=real#secret",
        "TELEGRAM_WEBHOOK_SECRET=real-secret # rotate",
    ],
)
def test_secret_helper_preserves_real_dotenv_forms(tmp_path, assignment):
    ensure_webhook_secret = load_secret_helper()
    env_path = tmp_path / ".env"
    env_path.write_text(f"{assignment}\n", encoding="utf-8")

    assert ensure_webhook_secret(env_path) is False
    assert env_path.read_text(encoding="utf-8") == f"{assignment}\n"


@pytest.mark.parametrize(
    "assignment",
    [
        "TELEGRAM_WEBHOOK_SECRET='replace_with_random_webhook_secret'",
        'TELEGRAM_WEBHOOK_SECRET="replace_with_random_webhook_secret"',
        'export TELEGRAM_WEBHOOK_SECRET = "replace_with_random_webhook_secret"',
        "TELEGRAM_WEBHOOK_SECRET = ''",
        "TELEGRAM_WEBHOOK_SECRET=replace_with_random_webhook_secret # rotate",
        'TELEGRAM_WEBHOOK_SECRET="replace_with_random_webhook_secret" # rotate',
        "TELEGRAM_WEBHOOK_SECRET='replace_with_random_webhook_secret' # rotate",
        'export TELEGRAM_WEBHOOK_SECRET = "change-me" # rotate',
        'TELEGRAM_WEBHOOK_SECRET="" # rotate',
        "TELEGRAM_WEBHOOK_SECRET= # rotate",
    ],
)
def test_secret_helper_replaces_quoted_placeholder_in_place(tmp_path, assignment):
    helper = load_secret_helper_module()
    env_path = tmp_path / ".env"
    env_path.write_text(f"{assignment}\n", encoding="utf-8")
    original_match = helper.parse_dotenv_assignment(assignment)
    _, original_comment = helper.split_dotenv_value(original_match.group("value"))

    assert helper.ensure_webhook_secret(env_path) is True

    matches = [
        match
        for line in env_path.read_text(encoding="utf-8").splitlines()
        if (match := helper.parse_dotenv_assignment(line)) is not None
        and match.group("key") == helper.KEY
    ]
    assert len(matches) == 1
    generated = helper.dotenv_value_for_analysis(matches[0].group("value"))
    assert generated
    assert generated not in helper.PLACEHOLDERS
    _, updated_comment = helper.split_dotenv_value(matches[0].group("value"))
    assert updated_comment == original_comment


def test_secret_helper_removes_duplicate_active_assignment_when_replacing(tmp_path):
    helper = load_secret_helper_module()
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TELEGRAM_WEBHOOK_SECRET=change-me\n"
        'export TELEGRAM_WEBHOOK_SECRET = "replace_with_random_webhook_secret" # rotate\n',
        encoding="utf-8",
    )

    assert helper.ensure_webhook_secret(env_path) is True

    matches = [
        match
        for line in env_path.read_text(encoding="utf-8").splitlines()
        if (match := helper.parse_dotenv_assignment(line)) is not None
        and match.group("key") == helper.KEY
    ]
    assert len(matches) == 1
    assert helper.split_dotenv_value(matches[0].group("value"))[1] == " # rotate"


@pytest.mark.parametrize(
    ("assignment", "expected"),
    [
        ("TELEGRAM_WEBHOOK_SECRET=value # comment", "value"),
        ('TELEGRAM_WEBHOOK_SECRET = "value" # comment', "value"),
        ("export TELEGRAM_WEBHOOK_SECRET='value' # comment", "value"),
        ('TELEGRAM_WEBHOOK_SECRET="real#secret"', "real#secret"),
        ("TELEGRAM_WEBHOOK_SECRET='real#secret'", "real#secret"),
        ("TELEGRAM_WEBHOOK_SECRET=real#secret", "real#secret"),
        ("TELEGRAM_WEBHOOK_SECRET=real-secret # rotate", "real-secret"),
        (
            "TELEGRAM_WEBHOOK_SECRET=replace_with_random_webhook_secret # rotate",
            "replace_with_random_webhook_secret",
        ),
        (
            'TELEGRAM_WEBHOOK_SECRET="replace_with_random_webhook_secret" # rotate',
            "replace_with_random_webhook_secret",
        ),
        (
            "TELEGRAM_WEBHOOK_SECRET='replace_with_random_webhook_secret' # rotate",
            "replace_with_random_webhook_secret",
        ),
        (
            'export TELEGRAM_WEBHOOK_SECRET = "change-me" # rotate',
            "change-me",
        ),
        ('TELEGRAM_WEBHOOK_SECRET="" # rotate', ""),
        ("TELEGRAM_WEBHOOK_SECRET= # rotate", ""),
    ],
)
def test_generator_and_preflight_analyze_dotenv_values_identically(
    tmp_path, assignment, expected
):
    helper = load_secret_helper_module()
    preflight = load_preflight_module()
    env_path = tmp_path / ".env"
    env_path.write_text(f"{assignment}\n", encoding="utf-8")
    match = helper.parse_dotenv_assignment(assignment)

    generator_value = helper.dotenv_value_for_analysis(match.group("value"))
    preflight_value = preflight.read_env(env_path)[helper.KEY]

    assert generator_value == expected
    assert preflight_value == expected


def test_secret_helper_cli_does_not_print_old_or_new_secret(tmp_path):
    helper = load_secret_helper_module()
    old_value = "replace_with_random_webhook_secret"
    env_path = tmp_path / ".env"
    env_path.write_text(
        f'export TELEGRAM_WEBHOOK_SECRET = "{old_value}" # rotate\n',
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(SECRET_HELPER), "--env-file", str(env_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    match = helper.parse_dotenv_assignment(
        env_path.read_text(encoding="utf-8").strip()
    )
    new_value = helper.dotenv_value_for_analysis(match.group("value"))

    assert result.returncode == 0
    assert old_value not in result.stdout + result.stderr
    assert new_value not in result.stdout + result.stderr


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
