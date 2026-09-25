import pytest
from pydantic import ValidationError

from backend.config import Settings


def _safe_production_settings(**overrides):
    values = {
        "app_env": "production",
        # CI keeps a local-development ADMIN_PASSWORD in the job environment.
        # Production configuration tests must explicitly prove that it is absent.
        "admin_password": "",
        "database_url": "postgresql+psycopg2://flashin:strong-db-password@db:5432/flashin",
        "cors_origins": "https://mini.flashin.store,https://admin.flashin.store",
        "telegram_bot_token": "1234567890:abcdefghijklmnopqrstuvwxyz",
        "jwt_secret": "j" * 48,
        "jwt_algorithm": "HS256",
        "jwt_expire_minutes": 30 * 24 * 60,
        "admin_jwt_expire_minutes": 480,
        "admin_totp_encryption_key": "t" * 48,
        "outbox_signing_secret": "o" * 48,
        "pilot_evidence_signing_secret": "p" * 48,
        "commercial_checkout_enabled": True,
        "payments_mode": "live",
        "pilot_runtime_enforced": True,
        "pilot_runtime_max_orders": 20,
        "payment_provider": "yookassa",
        "yookassa_shop_id": "shop-123",
        "yookassa_secret_key": "secret-123",
        "yookassa_return_url": "https://mini.flashin.store/payment-result",
        "media_storage": "local",
        "meilisearch_enabled": False,
        "moysklad_mode": "disabled",
        "moysklad_order_export_enabled": False,
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
    assert settings.admin_password == ""
    assert settings.admin_totp_encryption_key != settings.jwt_secret
    assert settings.pilot_runtime_enforced is True
    assert settings.pilot_runtime_max_orders == 20


def test_provider_disabled_production_does_not_require_commerce_credentials_or_pilot_state():
    settings = _safe_production_settings(
        commercial_checkout_enabled=False,
        payments_mode="disabled",
        pilot_runtime_enforced=False,
        pilot_evidence_signing_secret="",
        yookassa_shop_id="",
        yookassa_secret_key="",
        moysklad_mode="disabled",
        moysklad_order_export_enabled=False,
    )

    assert settings.commercial_checkout_enabled is False
    assert settings.payments_mode == "disabled"
    assert settings.moysklad_mode == "disabled"
    assert settings.pilot_runtime_enforced is False


def test_default_production_secrets_are_rejected():
    with pytest.raises(ValidationError) as exc_info:
        _safe_production_settings(
            jwt_secret="test-secret",
            admin_totp_encryption_key="change-me",
            outbox_signing_secret="change-me-outbox-secret",
            pilot_evidence_signing_secret="change-me",
        )

    message = str(exc_info.value)
    assert "JWT_SECRET" in message
    assert "ADMIN_TOTP_ENCRYPTION_KEY" in message
    assert "OUTBOX_SIGNING_SECRET" in message
    assert "PILOT_EVIDENCE_SIGNING_SECRET" in message


def test_production_rejects_persisted_admin_bootstrap_password():
    with pytest.raises(ValidationError) as exc_info:
        _safe_production_settings(admin_password="Strong-Admin-Password-2026")

    assert "ADMIN_PASSWORD must not be configured in production" in str(exc_info.value)


def test_totp_encryption_key_must_differ_from_jwt_secret():
    with pytest.raises(ValidationError) as exc_info:
        _safe_production_settings(
            jwt_secret="same-secret-material" * 3,
            admin_totp_encryption_key="same-secret-material" * 3,
        )

    assert "must differ from JWT_SECRET" in str(exc_info.value)


def test_pilot_evidence_secret_must_differ_from_other_secrets():
    with pytest.raises(ValidationError) as exc_info:
        _safe_production_settings(pilot_evidence_signing_secret="j" * 48)

    assert "PILOT_EVIDENCE_SIGNING_SECRET must differ from JWT_SECRET" in str(exc_info.value)


def test_production_requires_fail_closed_pilot_runtime():
    with pytest.raises(ValidationError) as disabled:
        _safe_production_settings(pilot_runtime_enforced=False)
    assert "PILOT_RUNTIME_ENFORCED" in str(disabled.value)

    with pytest.raises(ValidationError) as wrong_limit:
        _safe_production_settings(pilot_runtime_max_orders=19)
    assert "PILOT_RUNTIME_MAX_ORDERS" in str(wrong_limit.value)


def test_pilot_runtime_cannot_be_enabled_outside_production():
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            _env_file=None,
            app_env="staging",
            telegram_bot_token="test-token",
            jwt_secret="test-secret",
            pilot_runtime_enforced=True,
            pilot_runtime_max_orders=20,
        )

    assert "may only be true when APP_ENV=production" in str(exc_info.value)


def test_production_requires_explicit_https_cors_origins():
    with pytest.raises(ValidationError):
        _safe_production_settings(cors_origins="*")

    with pytest.raises(ValidationError):
        _safe_production_settings(cors_origins="http://mini.flashin.store")


def test_production_requires_yookassa_credentials_when_payments_are_enabled():
    with pytest.raises(ValidationError):
        _safe_production_settings(yookassa_shop_id="", yookassa_secret_key="")


def test_production_rejects_enabled_payments_when_checkout_is_disabled():
    with pytest.raises(ValidationError) as exc_info:
        _safe_production_settings(
            commercial_checkout_enabled=False,
            payments_mode="live",
            pilot_runtime_enforced=False,
        )

    assert "PAYMENTS_MODE must be disabled" in str(exc_info.value)


def test_enabled_moysklad_requires_sellable_store_even_without_order_export():
    with pytest.raises(ValidationError) as missing:
        _safe_production_settings(
            moysklad_mode="live",
            moysklad_token="token",
            moysklad_sale_price_type="RUB",
            moysklad_order_export_enabled=False,
            moysklad_store_id="",
        )
    assert "MOYSKLAD_STORE_ID is required when MOYSKLAD_MODE is enabled" in str(missing.value)

    accepted = _safe_production_settings(
        moysklad_mode="live",
        moysklad_token="token",
        moysklad_sale_price_type="RUB",
        moysklad_order_export_enabled=False,
        moysklad_store_id="sellable",
    )
    assert accepted.moysklad_store_id == "sellable"


def test_live_moysklad_export_requires_distinct_disposition_stores():
    with pytest.raises(ValidationError) as missing:
        _safe_production_settings(
            moysklad_mode="live",
            moysklad_token="token",
            moysklad_sale_price_type="RUB",
            moysklad_order_export_enabled=True,
            moysklad_organization_id="org",
            moysklad_agent_id="agent",
            moysklad_store_id="sellable",
            moysklad_delivery_service_id="delivery",
        )
    assert "MOYSKLAD_DAMAGED_STORE_ID" in str(missing.value)
    assert "MOYSKLAD_QUARANTINE_STORE_ID" in str(missing.value)

    with pytest.raises(ValidationError) as duplicate:
        _safe_production_settings(
            moysklad_mode="live",
            moysklad_token="token",
            moysklad_sale_price_type="RUB",
            moysklad_order_export_enabled=True,
            moysklad_organization_id="org",
            moysklad_agent_id="agent",
            moysklad_store_id="sellable",
            moysklad_damaged_store_id="sellable",
            moysklad_quarantine_store_id="quarantine",
            moysklad_delivery_service_id="delivery",
        )
    assert "must be three distinct stores" in str(duplicate.value)

    accepted = _safe_production_settings(
        moysklad_mode="live",
        moysklad_token="token",
        moysklad_sale_price_type="RUB",
        moysklad_order_export_enabled=True,
        moysklad_organization_id="org",
        moysklad_agent_id="agent",
        moysklad_store_id="sellable",
        moysklad_damaged_store_id="damaged",
        moysklad_quarantine_store_id="quarantine",
        moysklad_delivery_service_id="delivery",
    )
    assert accepted.moysklad_damaged_store_id == "damaged"
    assert accepted.moysklad_quarantine_store_id == "quarantine"


def test_runtime_provider_modes_are_explicit_enums():
    with pytest.raises(ValidationError):
        _safe_production_settings(payments_mode="maybe")

    with pytest.raises(ValidationError):
        _safe_production_settings(moysklad_mode="maybe")


def test_unsafe_jwt_algorithm_is_rejected_in_every_environment():
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            telegram_bot_token="test-token",
            jwt_secret="test-secret",
            jwt_algorithm="none",
        )
