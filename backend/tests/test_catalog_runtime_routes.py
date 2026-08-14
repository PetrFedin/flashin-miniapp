from backend.main import app


def _routes() -> set[tuple[str, str]]:
    result: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        for method in getattr(route, "methods", set()) or set():
            result.add((str(path), str(method)))
    return result


def test_catalog_runtime_mounts_public_and_admin_routes():
    routes = _routes()
    required = {
        ("/api/catalog/products", "GET"),
        ("/api/catalog/products/{product_id}", "GET"),
        ("/api/catalog/products/{product_id}/feedback", "POST"),
        ("/api/catalog/showroom/appointments", "POST"),
        ("/api/catalog/admin/products", "GET"),
        ("/api/catalog/admin/products", "POST"),
        ("/api/catalog/admin/products/{product_id}", "PUT"),
        ("/api/catalog/admin/showroom/appointments", "GET"),
    }
    assert required.issubset(routes), required - routes
