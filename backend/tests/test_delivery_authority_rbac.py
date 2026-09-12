from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api.delivery import create_delivery_zone, update_delivery_zone
from backend.api.delivery_providers import upsert_provider
from backend.delivery_schemas import DeliveryZoneAuthorityCreate, DeliveryZoneAuthorityUpdate
from backend.models import AdminRolePermission
from backend.schemas import DeliveryProviderIn
from backend.services.rbac import (
    DELIVERY_PROVIDERS_WRITE_PERMISSION,
    DELIVERY_TARIFFS_WRITE_PERMISSION,
)


class _EmptyPermissionQuery:
    def filter(self, *_args, **_kwargs):
        return self

    def all(self):
        return []


class _PermissionOnlyDb:
    """DB double that fails if a route reads commercial state before RBAC."""

    def query(self, model):
        assert model is AdminRolePermission, f"commercial DB access happened before RBAC: {model}"
        return _EmptyPermissionQuery()


@pytest.mark.parametrize("role", ["manager", "support", "warehouse"])
def test_low_privilege_admin_cannot_create_or_change_delivery_tariffs(role):
    db = _PermissionOnlyDb()
    admin = SimpleNamespace(role=role)
    create_payload = DeliveryZoneAuthorityCreate(
        name="Forbidden tariff",
        delivery_type="courier",
        price="500.00",
        priority=12345,
        city="Москва",
        provider_code="manual-courier",
        service_code="manual-courier",
    )

    with pytest.raises(HTTPException) as create_error:
        create_delivery_zone(create_payload, admin=admin, db=db)
    assert create_error.value.status_code == 403
    assert DELIVERY_TARIFFS_WRITE_PERMISSION in str(create_error.value.detail)

    with pytest.raises(HTTPException) as update_error:
        update_delivery_zone(
            1,
            DeliveryZoneAuthorityUpdate(price="600.00"),
            admin=admin,
            db=db,
        )
    assert update_error.value.status_code == 403
    assert DELIVERY_TARIFFS_WRITE_PERMISSION in str(update_error.value.detail)


@pytest.mark.parametrize("role", ["manager", "support", "warehouse"])
def test_low_privilege_admin_cannot_mutate_delivery_provider_config(role):
    db = _PermissionOnlyDb()
    admin = SimpleNamespace(role=role)
    payload = DeliveryProviderIn(
        code="manual-courier",
        name="Manual courier",
        active=True,
        config_json={"mode": "manual"},
    )

    with pytest.raises(HTTPException) as error:
        upsert_provider(payload, admin=admin, db=db)
    assert error.value.status_code == 403
    assert DELIVERY_PROVIDERS_WRITE_PERMISSION in str(error.value.detail)
