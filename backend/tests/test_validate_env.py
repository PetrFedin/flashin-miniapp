import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "validate_env.py"


def _valid_production_env() -> dict[str, str]:
    return {
        "APP_ENV": "production",
        "POSTGRES_DB": "flashin",
        "POSTGRES_USER": "flashin_app",
        "POSTGRES_PASSWORD": "db-password-2026",
        "DATABASE_URL": "postgresql+psycopg2://flashin_app:db-password-2026@db:5432/flashin",
        "CORS_ORIGINS": "https://mini.flashin.store,https://admin.flashin.store",
        "TELEGRAM_BOT_TOKEN": "telegram-token-value",
        "JWT_SECRET": "j" * 48,
        "ADMIN_EMAIL": "admin@flashin.store",
        "ADMIN_PASSWORD": "admin-password-2026",
        "MINI_APP_URL": "https://mini.flashin.store",
        "API_PUBLIC_URL": "https://api.flashin.store",
        "ADMIN_URL": "https://admin.flashin.store",
        "YOOKASSA_SHOP_ID": "shop-id",
        "YOOKASSA_SECRET_KEY": "yookassa-secret",
        "YOOKASSA_RETURN_URL": "https://mini.flashin.store/payment-result",
        "OUTBOX_SIGNING_SECRET": "o" * 48,
        "MEDIA_STORAGE": "r2",
        "MEDIA_PUBLIC_BASE_URL": "https://cdn.flashin.store",
        "S3_ENDPOINT_URL": "https://storage.example.com",
        "S3_BUCKET": "flashin",
        "S3_ACCESS_KEY_ID": "access-key",
        "S3_SECRET_ACCESS_KEY": "secret-key",
        "MEILISEARCH_ENABLED": "true",
        "MEILISEARCH_MASTER_KEY": "meili-master-key",
        "MOYSKLAD_TOKEN": "moysklad-token",
        "NOTIFICATION_BATCH_SIZE": "50",
        "NOTIFICATION_POLL_SECONDS": "10",
        "NOTIFICATION_MAX_ATTEMPTS": "5",
        "NOTIFICATION_INITIAL_BACKOFF_SECONDS": "30",
        "NOTIFICATION_MAX_BACKOFF_SECONDS": "3600",
    }


def _run_validator(tmp_path: Path, values: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env_text = "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"
    (tmp_path / ".env").write_text(env_text, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )


def test_valid_production_environment_passes(tmp_path):
    result = _run_validator(tmp_path, _valid_production_env())

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Environment OK" in result.stdout


def test_database_password_mismatch_is_rejected(tmp_path):
    values = _valid_production_env()
    values["POSTGRES_PASSWORD"] = "different-password"

    result = _run_validator(tmp_path, values)

    assert result.returncode == 1
    assert "DATABASE_URL password does not match POSTGRES_PASSWORD" in result.stdout


def test_backoff_order_is_rejected(tmp_path):
    values = _valid_production_env()
    values["NOTIFICATION_INITIAL_BACKOFF_SECONDS"] = "600"
    values["NOTIFICATION_MAX_BACKOFF_SECONDS"] = "60"

    result = _run_validator(tmp_path, values)

    assert result.returncode == 1
    assert "NOTIFICATION_MAX_BACKOFF_SECONDS must be >=" in result.stdout
