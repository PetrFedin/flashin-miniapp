from backend.main import app


_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}


def _routes() -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for path, path_item in app.openapi()["paths"].items():
        for method in path_item:
            if method.lower() in _HTTP_METHODS:
                result.add((str(path), str(method).upper()))
    return result


def test_catalog_runtime_mounts_public_and_admin_routes():
    routes = _routes()
    required = {
        ("/api/catalog/products", "GET"),
        ("/api/catalog/products/{product_id}", "GET"),
        ("/api/catalog/products/{product_id}/feedback", "POST"),
        ("/api/catalog/pricing", "GET"),
        ("/api/catalog/products/{product_id}/pricing", "GET"),
        ("/api/catalog/admin/pricing", "GET"),
        ("/api/catalog/admin/products/{product_id}/pricing", "GET"),
        ("/api/catalog/admin/products/{product_id}/pricing", "PATCH"),
        ("/api/catalog/showroom/appointments", "POST"),
        ("/api/catalog/intents/eligible-products", "GET"),
        ("/api/catalog/intents", "POST"),
        ("/api/catalog/intents/me", "GET"),
        ("/api/catalog/admin/intents", "GET"),
        ("/api/catalog/admin/intents/{intent_id}", "PATCH"),
        ("/api/catalog/admin/products", "GET"),
        ("/api/catalog/admin/products", "POST"),
        ("/api/catalog/admin/products/{product_id}", "PUT"),
        ("/api/catalog/admin/showroom/appointments", "GET"),
    }
    assert required.issubset(routes), required - routes
