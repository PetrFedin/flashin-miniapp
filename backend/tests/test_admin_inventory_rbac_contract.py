from pathlib import Path


def _admin_source() -> str:
    return (
        Path(__file__).resolve().parents[1] / "api" / "admin.py"
    ).read_text(encoding="utf-8")


def _route_block(source: str, marker: str, next_marker: str | None) -> str:
    block = source.split(marker, 1)[1]
    if next_marker:
        block = block.split(next_marker, 1)[0]
    return block


def test_product_creation_separates_catalog_and_inventory_permissions():
    source = _admin_source()
    block = _route_block(
        source,
        '@router.post("/products", response_model=ProductOut)',
        '@router.patch("/products/{product_id}/active", response_model=ProductOut)',
    )

    assert 'require_permission(db, admin, "products.write")' in block
    assert 'if any(stock_qty > 0 for stock_qty in initial_stocks):' in block
    assert 'require_permission(db, admin, "inventory.write")' in block
    assert 'stock_qty=0' in block
    assert 'reason="Product creation"' in block
    assert 'adjust_stock(' in block


def test_bulk_catalog_import_cannot_bypass_inventory_permission_or_ledger():
    source = _admin_source()
    block = _route_block(
        source,
        '@router.post("/products/import-csv")',
        '@router.get("/orders/export-csv")',
    )

    assert 'require_permission(db, admin, "products.write")' in block
    assert 'require_permission(db, admin, "inventory.write")' in block
    assert 'stock_qty=0' in block
    assert 'reason="CSV import"' in block
    assert block.count('adjust_stock(') >= 2


def test_manual_stock_update_remains_inventory_write_only():
    source = _admin_source()
    block = _route_block(
        source,
        '@router.patch("/variants/{variant_id}/stock")',
        '@router.get("/orders", response_model=list[OrderOut])',
    )

    assert 'require_permission(db, admin, "inventory.write")' in block
    assert 'require_permission(db, admin, "products.write")' not in block
    assert 'adjust_stock(' in block
