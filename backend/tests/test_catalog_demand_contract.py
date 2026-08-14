import inspect
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.api import catalog_demand
from backend.catalog_demand_models import ProductDemandRequest
from backend.services.rbac import DEFAULT_PERMISSIONS


def test_demand_payload_is_bounded_and_only_allows_supported_types():
    payload = catalog_demand.DemandRequestCreate(
        product_id=1,
        request_type="preorder",
        quantity=2,
    )
    assert payload.quantity == 2

    with pytest.raises(ValidationError):
        catalog_demand.DemandRequestCreate(
            product_id=1,
            request_type="in_stock",
            quantity=1,
        )
    with pytest.raises(ValidationError):
        catalog_demand.DemandRequestCreate(
            product_id=1,
            request_type="made_to_order",
            quantity=11,
        )


def test_demand_lane_is_deliberately_non_financial():
    source = inspect.getsource(catalog_demand)
    assert "Product has local stock; use the normal cart checkout flow" in source
    assert "Order(" not in source
    assert "Payment(" not in source
    assert "InventoryMovement(" not in source
    assert "reserve_inventory" not in source
    assert "create_payment" not in source


def test_demand_rbac_is_available_to_manager_and_support_but_not_warehouse():
    assert {"demand.read", "demand.write"} <= DEFAULT_PERMISSIONS["manager"]
    assert {"demand.read", "demand.write"} <= DEFAULT_PERMISSIONS["support"]
    assert "demand.read" not in DEFAULT_PERMISSIONS["warehouse"]
    assert "demand.write" not in DEFAULT_PERMISSIONS["warehouse"]


def test_active_request_uniqueness_and_indexes_match_migration_names():
    table = ProductDemandRequest.__table__
    constraints = {constraint.name for constraint in table.constraints if constraint.name}
    indexes = {index.name for index in table.indexes}
    assert "uq_product_demand_active_request" in constraints
    assert table.c.active_request_key.nullable is True
    assert indexes == {
        "ix_product_demand_customer",
        "ix_product_demand_product",
        "ix_product_demand_variant",
        "ix_product_demand_type",
        "ix_product_demand_status",
    }


def test_migration_0029_is_linear_from_catalog_merchandising_and_matches_indexes():
    migration = Path("backend/alembic/versions/0029_catalog_demand_requests.py").read_text(encoding="utf-8")
    assert 'revision = "0029_catalog_demand_requests"' in migration
    assert 'down_revision = "0028_catalog_merchandising"' in migration
    assert 'name="uq_product_demand_active_request"' in migration
    for name in (
        "ix_product_demand_customer",
        "ix_product_demand_product",
        "ix_product_demand_variant",
        "ix_product_demand_type",
        "ix_product_demand_status",
    ):
        assert name in migration
