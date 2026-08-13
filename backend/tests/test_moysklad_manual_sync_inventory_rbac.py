from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "api" / "moysklad.py"
SERVICE = ROOT / "services" / "moysklad.py"


def test_manual_moysklad_sync_requires_catalog_and_inventory_permissions():
    source = API.read_text(encoding="utf-8")
    route = source.split('@router.post("/sync", response_model=MoySkladSyncOut)', 1)[1].split(
        '@router.get("/operations-status")', 1
    )[0]

    assert 'require_permission(db, admin, "products.write")' in route
    assert 'require_permission(db, admin, "inventory.write")' in route
    assert 'admin_id=admin.id' in route


def test_moysklad_stock_changes_use_the_inventory_adjustment_boundary():
    source = SERVICE.read_text(encoding="utf-8")

    assert "from .inventory import adjust_stock" in source
    assert "def _apply_synced_stock(" in source
    assert "return adjust_stock(" in source
    assert 'reason=f"MoySklad {sync_type} sync"' in source
    assert "admin_id=admin_id" in source
    assert "stock_qty=external_stock if external_stock is not None else 0" not in source
    assert "variant.stock_qty = max(external_stock, variant.reserved_qty)" not in source
