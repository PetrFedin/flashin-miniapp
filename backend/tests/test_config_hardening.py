import pytest
from pydantic import ValidationError

from backend.config import Settings


def production_settings(**overrides) -> Settings:
    values = {
        "app_env": "production",
        "database_url": "postgresql+psycopg2://flashin:flashin@db:5432/flashin",
        "cors_origins": "https://mini.example.com,https://admin.example.com",
        "telegram_bot_token": "telegram-test-token",
        "telegram_webhook_secret": "w" * 32,
        "jwt_secret": "j" * 48,
        "admin_email": "admin@example.com",
        "admin_password": "p" * 20,
        "outbox_signing_secret": "o" * 48,
        "payment_provider": "manual",
        "mini_app_url": "https://mini.example.com",
        "api_public_url": "https://api.example.com",
        "yookassa_return_url": "https://mini.example.com/payment-result",
        "enable_seed": False,
        "use_create_all": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_secure_production_configuration_is_accepted():
    settings = production_settings()

    assert settings.app_env == "production"
    assert settings.cors_origin_list == [
        "https://mini.example.com",
        "https://admin.example.com",
    ]


@pytest.mark.parametrize(
    ("field", "value", "expected_message"),
    [
        ("telegram_webhook_secret", "", "TELEGRAM_WEBHOOK_SECRET"),
        ("jwt_secret", "short", "JWT_SECRET"),
        ("admin_password", "change-me-now", "ADMIN_PASSWORD"),
        ("outbox_signing_secret", "change-me-outbox-secret", "OUTBOX_SIGNING_SECRET"),
    ],
)
def test_unsafe_production_secrets_are_rejected(field, value, expected_message):
    with pytest.raises(ValidationError, match=expected_message):
        production_settings(**{field: value})


@pytest.mark.parametrize(
    "cors_origins",
    [
        "*",
        "https://mini.example.com,http://localhost:5174",
        "https://mini.example.com,http://127.0.0.1:5174",
    ],
)
def test_local_or_wildcard_production_cors_origins_are_rejected(cors_origins):
    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        production_settings(cors_origins=cors_origins)


@pytest.mark.parametrize(
    ("field", "value", "expected_message"),
    [
        ("mini_app_url", "http://mini.example.com", "MINI_APP_URL"),
        ("api_public_url", "http://api.example.com", "API_PUBLIC_URL"),
        ("yookassa_return_url", "http://mini.example.com/payment-result", "YOOKASSA_RETURN_URL"),
    ],
)
def test_production_public_urls_must_use_https(field, value, expected_message):
    with pytest.raises(ValidationError, match=expected_message):
        production_settings(**{field: value})


def test_yookassa_credentials_are_required_when_provider_is_enabled():
    with pytest.raises(ValidationError, match="YOOKASSA_SHOP_ID"):
        production_settings(
            payment_provider="yookassa",
            yookassa_shop_id="",
            yookassa_secret_key="",
        )


def test_object_storage_credentials_are_required_when_s3_is_enabled():
    with pytest.raises(ValidationError, match="S3_BUCKET"):
        production_settings(media_storage="s3")


@pytest.mark.parametrize("field", ["enable_seed", "use_create_all"])
def test_unsafe_database_bootstrap_modes_are_rejected_in_production(field):
    with pytest.raises(ValidationError, match=field.upper()):
        production_settings(**{field: True})
