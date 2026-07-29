import pytest
from pydantic import ValidationError

from backend.config import Settings


def _safe_production_settings(**overrides):
    values = {
        "app_env": "production",
        "database_url": "postgresql+psycopg2://flashin:strong-db-password@db:5432/flashin",
        "cors_origins": "https://mini.flashin.store,https://admin.flashin.store",
        "telegram_bot_token": "1234567890:abcdefghijklmnopqrstuvwxyz",
        "jwt_secret": "j" * 48,
        "jwt_algorithm": "HS256",
        "jwt_expire_minutes": 30 * 24 * 60,
        "admin_jwt_expire_minutes": 480,
        "admin_password": "Strong-Admin-Password-2026",
        "admin_totp_encryption_key": "t" * 48,
        "admin_mfa_required": True,
        "admin_mfa_setup_token_minutes": 10,
        "outbox_signing_secret": "o" * 48,
        "payment_provider": "yookassa",
        "yookassa_shop_id": "shop-123",
        "yookassa_secret_key": "secret-123",
        "yookassa_return_url": "https://mini.flashin.store/payment-result",
        "media_storage": "local",
        "meilisearch_enabled": False,
        "enable_seed": False,
        "use_create_all": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_safe_production_configuration_is_accepted():
    settings = _safe_production_settings()

    assert settings.app_env == "production"
    assert settings.jwt_algorithm == "HS256"
    assert settings.admin_jwt_expire_minutes == 480
    assert settings.admin_totp_encryption_key != settings.jwt_secret
    assert settings.admin_mfa_required is True
    assert settings.admin_mfa_setup_token_minutes == 10


def test_default_production_secrets_are_rejected():
    with pytest.raises(ValidationError) as exc_info:
        _safe_production_settings(
            jwt_secret="test-secret",
            admin_password="change-me-now",
            admin_totp_encryption_key="change-me",
            outbox_signing_secret="change-me-outbox-secret",
        )

    message = str(exc_info.value)
    assert "JWT_SECRET" in message
    assert "ADMIN_PASSWORD" in message
    assert "ADMIN_TOTP_ENCRYPTION_KEY" in message
    assert "OUTBOX_SIGNING_SECRET" in message


def test_totp_encryption_key_must_differ_from_jwt_secret():
    with pytest.raises(ValidationError) as exc_info:
        _safe_production_settings(
            jwt_secret="same-secret-material" * 3,
            admin_totp_encryption_key="same-secret-material" * 3,
        )

    assert "must differ from JWT_SECRET" in str(exc_info.value)


def test_production_requires_admin_mfa():
    with pytest.raises(ValidationError) as exc_info:
        _safe_production_settings(admin_mfa_required=False)

    assert "ADMIN_MFA_REQUIRED must be true" in str(exc_info.value)


def test_mfa_setup_token_lifetime_is_bounded_in_every_environment():
    for minutes in (4, 31):
        with pytest.raises(ValidationError) as exc_info:
            Settings(
                _env_file=None,
                telegram_bot_token="test-token",
                jwt_secret="test-secret",
                admin_mfa_setup_token_minutes=minutes,
            )
        assert "ADMIN_MFA_SETUP_TOKEN_MINUTES" in str(exc_info.value)


def test_production_requires_explicit_https_cors_origins():
    with pytest.raises(ValidationError):
        _safe_production_settings(cors_origins="*")

    with pytest.raises(ValidationError):
        _safe_production_settings(cors_origins="http://mini.flashin.store")


def test_production_requires_yookassa_credentials():
    with pytest.raises(ValidationError):
        _safe_production_settings(yookassa_shop_id="", yookassa_secret_key="")


def test_unsafe_jwt_algorithm_is_rejected_in_every_environment():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            telegram_bot_token="test-token",
            jwt_secret="test-secret",
            jwt_algorithm="none",
        )
