from fastapi import FastAPI
from fastapi.testclient import TestClient


class _Query:
    def __init__(self, product):
        self.product = product

    def options(self, *args):
        return self

    def filter(self, *args):
        return self

    def first(self):
        return self.product


class _Session:
    def __init__(self, product):
        self.product = product

    def query(self, model):
        from backend.models import Product

        assert model is Product
        return _Query(self.product)


def _product():
    from backend.models import Product, ProductImage, ProductVariant

    product = Product(
        id=1,
        sku="JACKET-1",
        title="Test Jacket",
        slug="test-jacket",
        brand="FLASHIN",
        description="",
        price=12990,
        currency="RUB",
        category="Jacket",
        gender="unisex",
        active=True,
    )
    product.images = [
        ProductImage(product_id=1, url="https://example.test/second.jpg", sort_order=2),
        ProductImage(product_id=1, url="https://example.test/first.jpg", sort_order=1),
    ]
    product.variants = [
        ProductVariant(product_id=1, size="M", color="blue", sku="JACKET-1-M-BLUE", stock_qty=2),
        ProductVariant(product_id=1, size="L", color="black", sku="JACKET-1-L-BLACK", stock_qty=1),
    ]
    return product


def test_fashion_ai_routes_registered():
    from backend.main import app

    routes = {
        (route.path, frozenset(route.methods))
        for route in app.routes
        if getattr(route, "methods", None)
    }
    assert ("/api/fashion-ai/products/{product_id}", frozenset({"GET"})) in routes
    assert ("/api/fashion-ai/products/{product_id}/match", frozenset({"POST"})) in routes


def test_product_style_returns_404_for_missing_product():
    from backend.api.fashion_ai import router
    from backend.database import get_db

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_db] = lambda: _Session(None)
    response = TestClient(test_app).get("/fashion-ai/products/404")
    assert response.status_code == 404
    assert response.json() == {"detail": "Product not found"}


def test_product_style_uses_product_image_and_variant_fields():
    from backend.api.fashion_ai import product_style

    result = product_style(1, _Session(_product()))
    assert result.product_id == 1
    assert result.title == "Test Jacket"
    assert result.image_url == "https://example.test/first.jpg"
    assert result.context["brand"] == "FLASHIN"
    assert result.context["category"] == "Jacket"
    assert result.context["color"] == "black"
    assert result.context["price"] == 12990
    assert result.context["tags"] == ["FLASHIN", "Jacket", "unisex", "black", "blue"]
    assert result.prompts[0] == "Create a premium fashion look using this Jacket."


def test_style_match_returns_calculated_score():
    from backend.api.fashion_ai import StyleMatchIn, style_match

    result = style_match(
        1,
        StyleMatchIn(preferences=["FLASHIN", "Jacket", "blue"]),
        _Session(_product()),
    )
    assert result.score == 100


def test_style_match_with_empty_preferences_returns_zero():
    from backend.api.fashion_ai import StyleMatchIn, style_match

    result = style_match(1, StyleMatchIn(preferences=[]), _Session(_product()))
    assert result.score == 0
