import inspect

from backend.api import ops


def test_manual_abandoned_cart_queue_reuses_guarded_job():
    source = inspect.getsource(ops.queue_abandoned_cart_notifications)

    assert 'require_permission(db, admin, "notifications.retry")' in source
    assert "run_sync_job(" in source
    assert '"abandoned-carts"' in source
    assert "queue_abandoned_cart_job" in source
    assert "Notification(" not in source


def test_manual_inventory_snapshot_reuses_guarded_job():
    source = inspect.getsource(ops.inventory_snapshot)

    assert 'require_permission(db, admin, "inventory.write")' in source
    assert "run_sync_job(" in source
    assert '"inventory-snapshot"' in source
    assert "create_inventory_snapshot" in source


def test_ops_read_endpoints_require_scoped_permissions():
    abandoned_source = inspect.getsource(ops.abandoned_carts)
    low_stock_source = inspect.getsource(ops.low_stock)

    assert 'require_permission(db, admin, "notifications.read")' in abandoned_source
    assert 'require_permission(db, admin, "products.read")' in low_stock_source
    assert ".limit(500)" in abandoned_source
    assert ".limit(1000)" in low_stock_source
