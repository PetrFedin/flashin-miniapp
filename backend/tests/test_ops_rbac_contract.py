from pathlib import Path


def _ops_source() -> str:
    return (
        Path(__file__).resolve().parents[1] / "api" / "ops.py"
    ).read_text(encoding="utf-8")


def _route_block(source: str, marker: str, next_marker: str | None) -> str:
    block = source.split(marker, 1)[1]
    if next_marker:
        block = block.split(next_marker, 1)[0]
    return block


def test_abandoned_cart_read_requires_customer_permission():
    source = _ops_source()
    block = _route_block(
        source,
        '@router.get("/abandoned-carts", response_model=list[AbandonedCartOut])',
        '@router.post("/abandoned-carts/queue-notifications")',
    )
    assert 'require_permission(db, admin, "customers.read")' in block


def test_abandoned_cart_notification_queue_requires_customer_and_notification_permissions():
    source = _ops_source()
    block = _route_block(
        source,
        '@router.post("/abandoned-carts/queue-notifications")',
        '@router.get("/inventory/low-stock", response_model=list[InventorySnapshotOut])',
    )
    assert 'require_permission(db, admin, "customers.read")' in block
    assert 'require_permission(db, admin, "notifications.retry")' in block


def test_low_stock_read_and_inventory_snapshot_have_distinct_permissions():
    source = _ops_source()
    low_stock = _route_block(
        source,
        '@router.get("/inventory/low-stock", response_model=list[InventorySnapshotOut])',
        '@router.post("/inventory/snapshot")',
    )
    snapshot = _route_block(source, '@router.post("/inventory/snapshot")', None)

    assert 'require_permission(db, admin, "products.read")' in low_stock
    assert 'require_permission(db, admin, "inventory.write")' not in low_stock
    assert 'require_permission(db, admin, "inventory.write")' in snapshot
