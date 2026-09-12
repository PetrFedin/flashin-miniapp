from pathlib import Path

from fastapi.routing import iter_route_contexts

from backend.main import app


ROOT = Path(__file__).resolve().parents[2]
FRONTEND_APP = ROOT / "frontend" / "src" / "App.jsx"
FRONTEND_API = ROOT / "frontend" / "src" / "api.js"
TELEGRAM_HOOK = ROOT / "frontend" / "src" / "hooks" / "useTelegram.js"


def _route_signatures() -> list[tuple[str, str]]:
    signatures: list[tuple[str, str]] = []
    for route in iter_route_contexts(app.routes):
        for method in route.methods or set():
            if method not in {"HEAD", "OPTIONS"}:
                signatures.append((method, route.path))
    return signatures


def _route_position(method: str, path: str) -> int:
    for index, route in enumerate(iter_route_contexts(app.routes)):
        if route.path == path and method in (route.methods or set()):
            return index
    raise AssertionError(f"Route not found: {method} {path}")


def test_application_has_no_duplicate_api_routes():
    signatures = _route_signatures()
    duplicates = sorted({signature for signature in signatures if signatures.count(signature) > 1})
    assert duplicates == []


def test_customer_journey_routes_are_connected():
    signatures = set(_route_signatures())
    required = {
        ("PATCH", "/api/cart/items/{item_id}"),
        ("DELETE", "/api/cart/items/{item_id}"),
        ("POST", "/api/orders/{order_id}/cancel-safe"),
        ("POST", "/api/admin/orders/{order_id}/cancel-safe"),
        ("POST", "/api/payments"),
        ("POST", "/api/returns"),
        ("GET", "/api/looks"),
        ("GET", "/api/wishlist"),
        ("POST", "/api/restock/subscribe"),
        ("GET", "/api/privacy/export"),
        ("POST", "/api/recommendations/size-helper/{product_id}"),
        ("GET", "/api/recommendations/personal/me"),
    }
    assert required.issubset(signatures)


def test_static_recommendation_routes_precede_dynamic_product_route():
    dynamic_position = _route_position("GET", "/api/recommendations/{product_id}")
    assert _route_position("GET", "/api/recommendations/personal/me") < dynamic_position
    assert _route_position("POST", "/api/recommendations/size-helper/{product_id}") < dynamic_position


def test_frontend_does_not_restore_known_dead_ends():
    source = FRONTEND_APP.read_text(encoding="utf-8")
    forbidden = (
        "alert(",
        "ID товаров",
        "Пока нет готовых образов",
        "onClick={() => createReturn",
        "sizeResult.explanation",
    )
    for fragment in forbidden:
        assert fragment not in source


def test_frontend_exposes_result_backed_actions():
    source = FRONTEND_APP.read_text(encoding="utf-8")
    api_source = FRONTEND_API.read_text(encoding="utf-8")
    hook_source = TELEGRAM_HOOK.read_text(encoding="utf-8")
    required_app_fragments = (
        "handleCartQuantity",
        "handleCartRemove",
        "handleOrderPayment",
        "handleOrderCancel",
        "handleReturn",
        "handlePrivacyExport",
        "look.products.map",
        "sizeHelper(selected.id",
        "initialized",
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
        "/api/recommendations/size-helper/${productId}",
    )
    for fragment in required_api_fragments:
        assert fragment in api_source

    assert "setInitialized(true)" in hook_source
