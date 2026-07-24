from types import SimpleNamespace

from backend.api.products import _commerce_card, _discount_percent, _safe_available_qty


def _variant(**overrides):
    data = {
        "id": 1,
        "sku": "SKU-1",
        "size": "M",
        "color": "Black",
        "stock_qty": 5,
        "reserved_qty": 2,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _product(**overrides):
    data = {
        "id": 10,
        "sku": "MODEL-10",
        "slug": "test-product",
        "title": "Test Product",
        "brand": "FLASHIN",
        "description": "A" * 100,
        "category": "Jackets",
        "gender": "unisex",
        "price": 8000,
        "old_price": 10000,
        "currency": "RUB",
        "is_drop": False,
        "is_rare": False,
        "drop_starts_at": None,
        "vip_only_until": None,
        "images": [
            SimpleNamespace(id=1, url="https://example.test/1.jpg", sort_order=0),
            SimpleNamespace(id=2, url="https://example.test/2.jpg", sort_order=1),
            SimpleNamespace(id=3, url="https://example.test/3.jpg", sort_order=2),
        ],
        "variants": [_variant()],
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_available_quantity_never_becomes_negative():
    assert _safe_available_qty(_variant(stock_qty=2, reserved_qty=5)) == 0
    assert _safe_available_qty(_variant(stock_qty=-3, reserved_qty=0)) == 0
    assert _safe_available_qty(_variant(stock_qty=None, reserved_qty=None)) == 0


def test_discount_percent_is_bounded_and_rejects_invalid_prices():
    assert _discount_percent(_product(price=8000, old_price=10000)) == 20
    assert _discount_percent(_product(price=0, old_price=10000)) == 0
    assert _discount_percent(_product(price=10000, old_price=8000)) == 0
    assert _discount_percent(_product(price=None, old_price=None)) == 0


def test_commerce_card_handles_empty_optional_text_and_bad_stock_data():
    product = _product(
        title=None,
        description=None,
        slug="product with spaces",
        variants=[_variant(color=None, size=None, stock_qty=1, reserved_qty=3)],
    )

    card = _commerce_card(product)

    assert card["options"]["total_available_qty"] == 0
    assert card["options"]["sold_out"] is True
    assert card["purchase"]["can_add_to_cart"] is False
    assert card["options"]["colors"][0]["name"] == "Основной"
    assert card["content_quality"]["checks"]["title"] is False
    assert card["content_quality"]["checks"]["description"] is False
    assert "%20" in card["telegram"]["mini_app_url"]


def test_product_without_variants_cannot_be_added_to_cart():
    card = _commerce_card(_product(variants=[]))

    assert card["purchase"]["requires_variant"] is False
    assert card["purchase"]["can_add_to_cart"] is False
    assert card["options"]["sold_out"] is True


def test_complete_available_product_is_publication_ready():
    card = _commerce_card(_product())

    assert card["content_quality"]["score"] == 100
    assert card["content_quality"]["ready_for_publication"] is True
    assert card["purchase"]["can_add_to_cart"] is True
    assert card["product"]["discount_percent"] == 20
