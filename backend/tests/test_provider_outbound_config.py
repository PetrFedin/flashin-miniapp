import pytest
from pydantic import ValidationError

from backend.config import Settings


@pytest.fixture(autouse=True)
def _clear_ci_admin_password(monkeypatch):
    """Production Settings tests must not inherit the development-only CI bootstrap password."""
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)


def _production(**overrides):
    values = {
        "app_env": "production",
        "admin_password": "",
        "database_url": "postgresql+psycopg2://flashin:strong-db-password@db:5432/flashin",
        "cors_origins": "https://mini.flashin.store,https://admin.flashin.store",
        "telegram_bot_token": "1234567890:abcdefghijklmnopqrstuvwxyz",
        "jwt_secret": "j" * 48,
        "admin_totp_encryption_key": "t" * 48,
        "outbox_signing_secret": "o" * 48,
        "pilot_evidence_signing_secret": "p" * 48,
        "pilot_runtime_enforced": True,
        "pilot_runtime_max_orders": 20,
        "payment_provider": "yookassa",
        "yookassa_shop_id": "shop-123",
        "yookassa_secret_key": "secret-123",
        "yookassa_return_url": "https://mini.flashin.store/payment-result",
        "media_storage": "local",
        "meilisearch_enabled": False,
        "enable_seed": False,
        "use_create_all": False,
        "moysklad_order_export_enabled": True,
        "moysklad_token": "moysklad-token",
        "moysklad_sale_price_type": "Retail",
        "moysklad_organization_id": "organization-id",
        "moysklad_agent_id": "agent-id",
        "moysklad_store_id": "store-id",
        "moysklad_delivery_service_id": "delivery-service-id",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_production_accepts_complete_moysklad_outbound_configuration():
    settings = _production()
    assert settings.moysklad_order_export_enabled is True
    assert settings.moysklad_organization_id == "organization-id"


@pytest.mark.parametrize(
    "field",
    [
        "moysklad_organization_id",
        "moysklad_agent_id",
        "moysklad_store_id",
        "moysklad_delivery_service_id",
    ],
)
def test_production_rejects_missing_moysklad_outbound_ids(field):
    with pytest.raises(ValidationError) as exc_info:
        _production(**{field: ""})
    assert field.upper() in str(exc_info.value)


def test_production_rejects_enabled_outbound_without_credentials():
    with pytest.raises(ValidationError) as exc_info:
        _production(moysklad_token="", moysklad_login="", moysklad_password="")
    assert "MoySklad credentials" in str(exc_info.value)


def test_production_rejects_non_https_moysklad_outbound_endpoint():
    with pytest.raises(ValidationError) as exc_info:
        _production(moysklad_base_url="http://moysklad.internal/api/remap/1.2")
    assert "MOYSKLAD_BASE_URL must use HTTPS" in str(exc_info.value)
