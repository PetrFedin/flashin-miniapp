from types import SimpleNamespace

from backend.api.products import _commerce_card, router
from backend.services.pricing import quote_product_price


def test_public_catalog_router_is_read_only():
    mutation_methods = {"POST", "PUT", "PATCH", "DELETE"}
    exposed_mutations = [
        (route.path, sorted(set(route.methods or set()) & mutation_methods))
        for route in router.routes
        if set(route.methods or set()) & mutation_methods
    ]
    assert exposed_mutations == []


def test_commerce_card_does_not_advertise_unimplemented_purchase_flows():
    product = SimpleNamespace(
        id=1,
        sku="FLASHIN-001",
        slug="flashin-001",
        title="FLASHIN Coat",
        brand="FLASHIN",
        description="A" * 100,
        category="Outerwear",
        gender="unisex",
        price=10000.0,
        old_price=None,
        currency="RUB",
        is_drop=False,
        is_rare=False,
        drop_starts_at=None,
        vip_only_until=None,
        images=[],
        variants=[],
    )
    pricing = quote_product_price(product)

    purchase = _commerce_card(product, pricing)["purchase"]
    assert purchase["supports_gift_order"] is False
    assert purchase["supports_telegram_stars"] is False