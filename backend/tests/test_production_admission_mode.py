import pytest
from pydantic import ValidationError


def _base_env(monkeypatch):
    values = {
        "APP_ENV": "production",
        "PRODUCTION_ADMISSION_MODE": "true",
        "DATABASE_URL": "postgresql+psycopg2://flashin:strong-password@db.example:5432/flashin",
        "CORS_ORIGINS": "https://mini.example.test,https://admin.example.test",
        "TELEGRAM_BOT_TOKEN": "",
        "JWT_SECRET": "j" * 40,
        "ADMIN_TOTP_ENCRYPTION_KEY": "t" * 40,
        "OUTBOX_SIGNING_SECRET": "o" * 40,
        "COMMERCIAL_CHECKOUT_ENABLED": "false",
        "PAYMENTS_MODE": "disabled",
        "MOYSKLAD_MODE": "disabled",
        "MOYSKLAD_ORDER_EXPORT_ENABLED": "false",
        "PILOT_RUNTIME_ENFORCED": "false",
        "RATE_LIMIT_ENABLED": "true",
        "RATE_LIMIT_BACKEND": "redis",
        "RATE_LIMIT_REDIS_URL": "redis://redis.example:6379/0",
        "USE_CREATE_ALL": "false",
        "ENABLE_SEED": "false",
        "MEDIA_STORAGE": "local",
        "MEILISEARCH_ENABLED": "false",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def _settings(monkeypatch, **overrides):
    _base_env(monkeypatch)
    for key, value in overrides.items():
        monkeypatch.setenv(key, str(value).lower() if isinstance(value, bool) else str(value))
    from backend.config import Settings
    return Settings()


def test_provider_disabled_admission_allows_missing_telegram_token(monkeypatch):
    settings = _settings(monkeypatch)
    assert settings.production_admission_mode is True
    assert settings.telegram_bot_token == ""
    assert settings.commercial_checkout_enabled is False
    assert settings.payments_mode == "disabled"
    assert settings.moysklad_mode == "disabled"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("COMMERCIAL_CHECKOUT_ENABLED", True),
        ("PAYMENTS_MODE", "sandbox"),
        ("MOYSKLAD_MODE", "sandbox"),
        ("MOYSKLAD_ORDER_EXPORT_ENABLED", True),
        ("PILOT_RUNTIME_ENFORCED", True),
    ],
)
def test_admission_mode_fails_closed_if_commerce_or_provider_execution_is_enabled(
    monkeypatch, name, value
):
    with pytest.raises(ValidationError):
        _settings(monkeypatch, **{name: value})


def test_normal_production_still_requires_telegram_token(monkeypatch):
    with pytest.raises(ValidationError):
        _settings(monkeypatch, PRODUCTION_ADMISSION_MODE=False)
