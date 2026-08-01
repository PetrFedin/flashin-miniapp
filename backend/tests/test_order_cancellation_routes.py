from pathlib import Path

from backend import main


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _post_routes(path: str):
    return [
        route
        for route in main.app.routes
        if getattr(route, "path", "") == path
        and "POST" in getattr(route, "methods", set())
    ]


def test_customer_cancel_has_one_canonical_runtime_route():
    routes = _post_routes("/api/orders/{order_id}/cancel")

    assert len(routes) == 1
    assert routes[0].endpoint.__module__ == "backend.api.order_cancellation"


def test_admin_cancel_has_one_canonical_runtime_route():
    routes = _post_routes("/api/admin/orders/{order_id}/cancel")

    assert len(routes) == 1
    assert routes[0].endpoint.__module__ == "backend.api.order_cancellation"


def test_legacy_safe_aliases_are_hidden_from_openapi():
    paths = main.app.openapi()["paths"]

    assert "/api/orders/{order_id}/cancel" in paths
    assert "/api/admin/orders/{order_id}/cancel" in paths
    assert "/api/orders/{order_id}/cancel-safe" not in paths
    assert "/api/admin/orders/{order_id}/cancel-safe" not in paths


def test_customer_cancel_rewrite_middleware_is_removed():
    source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")

    assert "CustomerOrderCancelGuardMiddleware" not in source


def test_legacy_customer_cancel_handler_and_route_pruning_are_removed():
    orders_source = (BACKEND_ROOT / "api" / "orders.py").read_text(encoding="utf-8")
    main_source = (BACKEND_ROOT / "main.py").read_text(encoding="utf-8")

    assert "def cancel_order(" not in orders_source
    assert "orders_router.routes[:]" not in main_source
