from pathlib import Path

from backend.services.rbac import (
    DEFAULT_PERMISSIONS,
    DELIVERY_PROVIDERS_WRITE_PERMISSION,
)


ROOT = Path(__file__).resolve().parents[1]
FULFILLMENT_API = ROOT / "api" / "fulfillment.py"
DELIVERY_API = ROOT / "api" / "delivery_providers.py"


def test_default_roles_separate_physical_fulfillment_from_financial_order_write():
    assert "fulfillment.write" in DEFAULT_PERMISSIONS["manager"]
    assert "fulfillment.write" in DEFAULT_PERMISSIONS["warehouse"]
    assert "orders.write" not in DEFAULT_PERMISSIONS["warehouse"]
    assert "fulfillment.write" not in DEFAULT_PERMISSIONS["support"]


def test_picklist_and_task_mutations_require_fulfillment_write():
    source = FULFILLMENT_API.read_text(encoding="utf-8")
    update_task = source.split('@router.patch("/tasks/{task_id}"', 1)[1].split(
        '@router.get("/sla"', 1
    )[0]
    update_item = source.split('@router.patch("/task-items/{task_item_id}"', 1)[1]

    assert 'require_permission(db, admin, "fulfillment.write")' in update_task
    assert 'require_permission(db, admin, "orders.write")' not in update_task
    assert 'require_permission(db, admin, "fulfillment.write")' in update_item
    assert 'require_permission(db, admin, "orders.write")' not in update_item


def test_shipment_mutations_and_provider_configuration_have_separate_authority():
    source = DELIVERY_API.read_text(encoding="utf-8")
    provider_config = source.split('@router.post("", response_model=DeliveryProviderOut)', 1)[1].split(
        '@router.post("/orders/{order_id}/shipment"', 1
    )[0]
    create_shipment = source.split('@router.post("/orders/{order_id}/shipment"', 1)[1].split(
        '@router.patch("/shipments/{shipment_id}"', 1
    )[0]
    patch_shipment = source.split('@router.patch("/shipments/{shipment_id}"', 1)[1].split(
        '@router.get("/shipments"', 1
    )[0]

    assert DELIVERY_PROVIDERS_WRITE_PERMISSION == "delivery.providers.write"
    assert "DELIVERY_PROVIDERS_WRITE_PERMISSION" in provider_config
    assert 'require_permission(db, admin, "orders.write")' not in provider_config
    assert 'require_permission(db, admin, "fulfillment.write")' not in provider_config
    assert 'require_permission(db, admin, "fulfillment.write")' in create_shipment
    assert 'require_permission(db, admin, "orders.write")' not in create_shipment
    assert 'require_permission(db, admin, "fulfillment.write")' in patch_shipment
    assert 'require_permission(db, admin, "orders.write")' not in patch_shipment
