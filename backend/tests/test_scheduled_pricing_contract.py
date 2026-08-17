from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_cart_reconciliation_and_serialization_share_effective_unit_prices():
    adjustments = source("backend/services/cart_adjustments.py")
    cart = source("backend/api/cart.py")

    assert "load_product_price_quotes" in adjustments
    assert "quote.effective_price" in adjustments
    assert "unit_prices=unit_prices" in adjustments
    assert "adjustments.unit_prices" in cart
    assert "Cart changed during adjustment reconciliation" in cart


def test_checkout_locks_pricing_once_and_snapshots_same_price_into_order_items():
    orders = source("backend/api/orders.py")

    assert "load_product_price_quotes" in orders
    assert "lock=True" in orders
    assert "price_quotes[int(item.product_id)].effective_price" in orders
    assert "price=float(quote.effective_price)" in orders
    assert "product.price" not in orders.split("subtotal = sum(", 1)[1].split("promo, discount", 1)[0]


def test_legacy_products_and_catalog_plus_use_canonical_pricing_source():
    products = source("backend/api/products.py")
    catalog_api = source("frontend/src/catalogApi.js")

    assert "load_product_price_quotes" in products
    assert 'payload["price"] = float(pricing.effective_price)' in products
    assert "/api/catalog/pricing?" in catalog_api
    assert "/api/catalog/products/${id}/pricing" in catalog_api
    assert "applyPricing" in catalog_api


def test_pricing_api_separates_public_and_admin_configuration():
    api = source("backend/api/catalog_pricing.py")
    pricing = source("backend/services/pricing.py")

    assert '@router.get("/pricing")' in api
    assert '@router.get("/admin/pricing")' in api
    assert 'require_permission(db, admin, "products.write")' in api
    assert "payload.model_fields_set" in api
    assert "public_payload" in pricing
    assert "admin_payload" in pricing
