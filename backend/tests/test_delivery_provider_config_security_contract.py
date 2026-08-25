import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api.delivery_providers import _public_provider, _validated_provider_config
from backend.services.rbac import DELIVERY_PROVIDERS_WRITE_PERMISSION, DEFAULT_PERMISSIONS


def _source() -> str:
    return (
        Path(__file__).resolve().parents[1] / "api" / "delivery_providers.py"
    ).read_text(encoding="utf-8")


def test_operational_provider_response_never_returns_stored_configuration():
    provider = SimpleNamespace(
        id=7,
        code="courier",
        name="Courier",
        active=True,
        config_json='{"api_key":"must-never-leak","timeout_seconds":30}',
    )

    assert _public_provider(provider) == {
        "id": 7,
        "code": "courier",
        "name": "Courier",
        "active": True,
        "config_json": "{}",
    }


def test_provider_config_rejects_nested_credentials():
    with pytest.raises(HTTPException) as error:
        _validated_provider_config(
            {
                "endpoint": "https://delivery.invalid",
                "auth": {"client_secret": "do-not-store-here"},
            }
        )

    assert error.value.status_code == 400
    assert "secret-managed" in str(error.value.detail)


@pytest.mark.parametrize(
    "key",
    ["token", "api-key", "vendor_password", "private key", "refresh_token"],
)
def test_provider_config_rejects_sensitive_key_variants(key):
    with pytest.raises(HTTPException) as error:
        _validated_provider_config({key: "sensitive"})

    assert error.value.status_code == 400


def test_provider_config_allows_bounded_non_secret_metadata():
    encoded = _validated_provider_config(
        {
            "label_format": "pdf",
            "timeout_seconds": 30,
            "options": {"pickup_points": True},
        }
    )

    assert json.loads(encoded) == {
        "label_format": "pdf",
        "options": {"pickup_points": True},
        "timeout_seconds": 30,
    }


def test_provider_config_size_is_bounded():
    with pytest.raises(HTTPException) as error:
        _validated_provider_config({"metadata": "x" * (8 * 1024)})

    assert error.value.status_code == 413


def test_provider_configuration_uses_dedicated_write_permission_and_audit():
    source = _source()
    upsert_source = source.split("def upsert_provider", 1)[1].split(
        '@router.post("/orders/{order_id}/shipment"', 1
    )[0]

    assert "DELIVERY_PROVIDERS_WRITE_PERMISSION" in upsert_source
    assert 'require_permission(db, admin, "orders.write")' not in upsert_source
    assert '"delivery.provider.upsert"' in upsert_source
    assert '"config_changed": True' in upsert_source
    assert "return _public_provider(row)" in upsert_source


def test_default_operational_roles_do_not_receive_provider_configuration_authority():
    assert DELIVERY_PROVIDERS_WRITE_PERMISSION == "delivery.providers.write"
    for role in ("manager", "support", "warehouse"):
        assert DELIVERY_PROVIDERS_WRITE_PERMISSION not in DEFAULT_PERMISSIONS[role]
    assert "*" in DEFAULT_PERMISSIONS["owner"]
