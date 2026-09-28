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
        "ADMIN_TOTP_ENCRYPTION_KEY": "t" * 48,
        "MINI_APP_URL": "https://mini.flashin.store",
        "API_PUBLIC_URL": "https://api.flashin.store",
        "ADMIN_URL": "https://admin.flashin.store",
        "COMMERCIAL_CHECKOUT_ENABLED": "true",
        "PAYMENTS_MODE": "live",
        "YOOKASSA_SHOP_ID": "shop-id",
        "YOOKASSA_SECRET_KEY": "yookassa-secret",
        "YOOKASSA_RETURN_URL": "https://mini.flashin.store/payment-result",
        "OUTBOX_SIGNING_SECRET": "o" * 48,
        "PILOT_EVIDENCE_SIGNING_SECRET": "p" * 48,
        "PILOT_PROVIDER_EVIDENCE_MAX_AGE_MINUTES": "60",
        "PILOT_LIVE_GATE_MAX_AGE_MINUTES": "30",
        "PILOT_ADMISSION_MAX_AGE_MINUTES": "60",
        "PILOT_ROLLBACK_DRILL_MAX_AGE_DAYS": "30",
        "PILOT_RUNTIME_ENFORCED": "true",
        "PILOT_RUNTIME_MAX_ORDERS": "20",
        "MEDIA_STORAGE": "r2",
        "MEDIA_PUBLIC_BASE_URL": "https://cdn.flashin.store",
        "S3_ENDPOINT_URL": "https://storage.example.com",
        "S3_BUCKET": "flashin",
        "S3_ACCESS_KEY_ID": "access-key",
        "S3_SECRET_ACCESS_KEY": "secret-key",
        "MEILISEARCH_ENABLED": "true",
        "MEILISEARCH_MASTER_KEY": "meili-master-key",
        "MOYSKLAD_MODE": "live",
        "MOYSKLAD_TOKEN": "moysklad-token",
        "MOYSKLAD_SALE_PRICE_TYPE": "Розничная цена",
        "MOYSKLAD_SIZE_ATTRIBUTE_NAMES": "Размер,Size",
        "MOYSKLAD_COLOR_ATTRIBUTE_NAMES": "Цвет,Color",
        "MOYSKLAD_SYNC_INTERVAL_MINUTES": "30",
        "SCHEDULER_ENABLED": "true",
        "RATE_LIMIT_ENABLED": "true",
        "RATE_LIMIT_BACKEND": "redis",
        "RATE_LIMIT_REDIS_URL": "redis://redis:6379/0",
        "RATE_LIMIT_REDIS_CONNECT_TIMEOUT_SECONDS": "1",
        "RATE_LIMIT_REDIS_SOCKET_TIMEOUT_SECONDS": "1",
        "RATE_LIMIT_PER_MINUTE": "120",
        "RATE_LIMIT_AUTH_PER_MINUTE": "20",
        "RATE_LIMIT_ADMIN_LOGIN_PER_MINUTE": "10",
        "RATE_LIMIT_SEARCH_PER_MINUTE": "120",
        "RATE_LIMIT_CHECKOUT_PER_MINUTE": "12",
        "RATE_LIMIT_PAYMENT_PER_MINUTE": "12",
        "RATE_LIMIT_RETURN_PER_MINUTE": "12",
        "RATE_LIMIT_SUPPORT_PER_MINUTE": "30",
        "RATE_LIMIT_WEBHOOK_PER_MINUTE": "180",
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


def test_provider_disabled_production_passes_without_commerce_provider_credentials(tmp_path):
    values = _valid_production_env()
    values["COMMERCIAL_CHECKOUT_ENABLED"] = "false"
    values["PAYMENTS_MODE"] = "disabled"
    values["PILOT_RUNTIME_ENFORCED"] = "false"
    values.pop("PILOT_EVIDENCE_SIGNING_SECRET", None)
    values.pop("PILOT_PROVIDER_EVIDENCE_MAX_AGE_MINUTES", None)
    values.pop("PILOT_LIVE_GATE_MAX_AGE_MINUTES", None)
    values.pop("PILOT_ADMISSION_MAX_AGE_MINUTES", None)
    values.pop("PILOT_ROLLBACK_DRILL_MAX_AGE_DAYS", None)
    values.pop("PILOT_RUNTIME_MAX_ORDERS", None)
    values.pop("YOOKASSA_SHOP_ID", None)
    values.pop("YOOKASSA_SECRET_KEY", None)
    values.pop("YOOKASSA_RETURN_URL", None)
    values["MOYSKLAD_MODE"] = "disabled"
    values.pop("MOYSKLAD_TOKEN", None)
    values.pop("MOYSKLAD_SALE_PRICE_TYPE", None)
    values["MEILISEARCH_ENABLED"] = "false"
    values.pop("MEILISEARCH_MASTER_KEY", None)

    result = _run_validator(tmp_path, values)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Environment OK" in result.stdout
    assert "'payments_mode': 'disabled'" in result.stdout
    assert "'moysklad_mode': 'disabled'" in result.stdout


def test_persisted_production_admin_password_is_rejected(tmp_path):
    values = _valid_production_env()
    values["ADMIN_PASSWORD"] = "Strong-Admin-Password-2026"

    result = _run_validator(tmp_path, values)

    assert result.returncode == 1
    assert "ADMIN_PASSWORD must not be stored in production" in result.stdout


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


def test_non_distributed_production_rate_limiter_is_rejected(tmp_path):
    values = _valid_production_env()
    values["RATE_LIMIT_BACKEND"] = "memory"

    result = _run_validator(tmp_path, values)

    assert result.returncode == 1
    assert "RATE_LIMIT_BACKEND must be redis in production" in result.stdout


def test_invalid_production_rate_limit_redis_url_is_rejected(tmp_path):
    values = _valid_production_env()
    values["RATE_LIMIT_REDIS_URL"] = "http://redis:6379/0"

    result = _run_validator(tmp_path, values)

    assert result.returncode == 1
    assert "RATE_LIMIT_REDIS_URL must use redis:// or rediss://" in result.stdout


def test_disabled_production_rate_limiter_is_rejected(tmp_path):
    values = _valid_production_env()
    values["RATE_LIMIT_ENABLED"] = "false"

    result = _run_validator(tmp_path, values)

    assert result.returncode == 1
    assert "RATE_LIMIT_ENABLED must be true in production" in result.stdout


def test_disabled_production_scheduler_is_rejected(tmp_path):
    values = _valid_production_env()
    values["SCHEDULER_ENABLED"] = "false"

    result = _run_validator(tmp_path, values)

    assert result.returncode == 1
    assert "SCHEDULER_ENABLED must be true in production" in result.stdout


def test_moysklad_interval_outside_safe_range_is_rejected(tmp_path):
    values = _valid_production_env()
    values["MOYSKLAD_SYNC_INTERVAL_MINUTES"] = "1"

    result = _run_validator(tmp_path, values)

    assert result.returncode == 1
    assert "MOYSKLAD_SYNC_INTERVAL_MINUTES must be between 5 and 1440" in result.stdout


def test_missing_moysklad_sale_price_type_is_rejected(tmp_path):
    values = _valid_production_env()
    values["MOYSKLAD_SALE_PRICE_TYPE"] = ""

    result = _run_validator(tmp_path, values)

    assert result.returncode == 1
    assert "MOYSKLAD_SALE_PRICE_TYPE" in result.stdout


def test_short_pilot_evidence_secret_is_rejected(tmp_path):
    values = _valid_production_env()
    values["PILOT_EVIDENCE_SIGNING_SECRET"] = "too-short"

    result = _run_validator(tmp_path, values)

    assert result.returncode == 1
    assert "PILOT_EVIDENCE_SIGNING_SECRET must contain at least 32 characters" in result.stdout


def test_reused_pilot_evidence_secret_is_rejected(tmp_path):
    values = _valid_production_env()
    values["PILOT_EVIDENCE_SIGNING_SECRET"] = values["JWT_SECRET"]

    result = _run_validator(tmp_path, values)

    assert result.returncode == 1
    assert "PILOT_EVIDENCE_SIGNING_SECRET must differ from JWT_SECRET" in result.stdout


def test_pilot_evidence_ttl_outside_safe_range_is_rejected(tmp_path):
    values = _valid_production_env()
    values["PILOT_LIVE_GATE_MAX_AGE_MINUTES"] = "121"

    result = _run_validator(tmp_path, values)

    assert result.returncode == 1
    assert "PILOT_LIVE_GATE_MAX_AGE_MINUTES must be between 5 and 120" in result.stdout


def test_disabled_pilot_runtime_is_rejected_when_commercial_checkout_is_enabled(tmp_path):
    values = _valid_production_env()
    values["PILOT_RUNTIME_ENFORCED"] = "false"

    result = _run_validator(tmp_path, values)

    assert result.returncode == 1
    assert "PILOT_RUNTIME_ENFORCED must be true for production commercial checkout" in result.stdout


def test_pilot_runtime_limit_must_equal_twenty(tmp_path):
    values = _valid_production_env()
    values["PILOT_RUNTIME_MAX_ORDERS"] = "19"

    result = _run_validator(tmp_path, values)

    assert result.returncode == 1
    assert "PILOT_RUNTIME_MAX_ORDERS must equal 20 for production commercial checkout" in result.stdout
