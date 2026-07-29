import ipaddress

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
        "outbox_signing_secret": "o" * 48,
        "payment_provider": "yookassa",
        "yookassa_shop_id": "shop-123",
        "yookassa_secret_key": "secret-123",
        "yookassa_return_url": "https://mini.flashin.store/payment-result",
        "media_storage": "local",
        "meilisearch_enabled": False,
        "enable_seed": False,
        "use_create_all": False,
        "proxy_trusted_hops": 1,
        "proxy_trusted_cidrs": "10.0.0.0/8,172.16.0.0/12",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_safe_production_configuration_is_accepted():
    settings = _safe_production_settings()

    assert settings.app_env == "production"
    assert settings.jwt_algorithm == "HS256"
    assert settings.admin_jwt_expire_minutes == 480
    assert settings.admin_totp_encryption_key != settings.jwt_secret
    assert settings.proxy_trusted_hops == 1
    assert settings.proxy_trusted_networks == (
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
    )


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


def test_proxy_hop_count_is_bounded_in_every_environment():
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            telegram_bot_token="test-token",
            jwt_secret="test-secret",
            proxy_trusted_hops=11,
        )

    assert "PROXY_TRUSTED_HOPS" in str(exc_info.value)


def test_invalid_proxy_cidr_is_rejected_in_every_environment():
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            telegram_bot_token="test-token",
            jwt_secret="test-secret",
            proxy_trusted_hops=1,
            proxy_trusted_cidrs="10.0.0.0/8,not-a-network",
        )

    assert "PROXY_TRUSTED_CIDRS" in str(exc_info.value)


def test_proxy_networks_cannot_be_empty_when_hops_are_trusted():
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            telegram_bot_token="test-token",
            jwt_secret="test-secret",
            proxy_trusted_hops=1,
            proxy_trusted_cidrs="",
        )

    assert "PROXY_TRUSTED_CIDRS cannot be empty" in str(exc_info.value)


def test_production_rate_limit_requires_at_least_one_proxy_hop():
    with pytest.raises(ValidationError) as exc_info:
        _safe_production_settings(proxy_trusted_hops=0, rate_limit_enabled=True)

    assert "PROXY_TRUSTED_HOPS must be at least 1" in str(exc_info.value)


def test_production_without_rate_limit_can_disable_proxy_trust():
    settings = _safe_production_settings(
        rate_limit_enabled=False,
        proxy_trusted_hops=0,
        proxy_trusted_cidrs="",
    )

    assert settings.proxy_trusted_hops == 0
    assert settings.proxy_trusted_networks == ()
