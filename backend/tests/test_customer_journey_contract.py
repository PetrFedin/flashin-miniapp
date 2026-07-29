from pathlib import Path

from fastapi.routing import APIRoute

from backend.main import app


ROOT = Path(__file__).resolve().parents[2]
FRONTEND_APP = ROOT / "frontend" / "src" / "App.js"
FRONTEND_API = ROOT / "frontend" / "src" / "api.js"


def _route_signatures() -> list[tuple[str, str]]:
    signatures: list[tuple[str, str]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or set():
            if method not in {"HEAD", "OPTIONS"}:
                signatures.append((method, route.path))
    return signatures


def test_application_has_no_duplicate_api_routes():
    signatures = _route_signatures()
    duplicates = sorted({signature for signature in signatures if signatures.count(signature) > 1})
    assert duplicates == []


def test_customer_journey_routes_are_connected():
    signatures = set(_route_signatures())
    required = {
        ("PATCH", "/api/cart/items/{item_id}"),
        ("DELETE", "/api/cart/items/{item_id}"),
        ("POST", "/api/orders/{order_id}/cancel"),
        ("POST", "/api/payments"),
        ("POST", "/api/returns"),
        ("GET", "/api/looks"),
        ("GET", "/api/wishlist"),
        ("POST", "/api/restock/subscribe"),
        ("GET", "/api/privacy/export"),
    }
    assert required.issubset(signatures)


def test_frontend_does_not_restore_known_dead_ends():
    source = FRONTEND_APP.read_text(encoding="utf-8")
    forbidden = (
        "alert(",
        "ID товаров",
        "Пока нет готовых образов",
        "onClick={() => createReturn",
    )
    for fragment in forbidden:
        assert fragment not in source


def test_frontend_exposes_result_backed_actions():
    source = FRONTEND_APP.read_text(encoding="utf-8")
    api_source = FRONTEND_API.read_text(encoding="utf-8")
    required_app_fragments = (
        "handleCartQuantity",
        "handleCartRemove",
        "handleOrderPayment",
        "handleOrderCancel",
        "handleReturn",
        "handlePrivacyExport",
        "look.products.map",
        "listWishlist",
    )
    for fragment in required_app_fragments:
        assert fragment in source

    required_api_fragments = (
        "updateCartItem",
        "removeCartItem",
        "cancelOrder",
        "createPayment",
        "downloadPrivacyData",
        "listWishlist",
    )
    for fragment in required_api_fragments:
        assert fragment in api_source
