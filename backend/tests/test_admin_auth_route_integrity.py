from fastapi.routing import APIRoute

from backend.api.admin import router as legacy_admin_router
from backend.main import app


def _post_routes(routes, path: str):
    return [
        route
        for route in routes
        if isinstance(route, APIRoute)
        and route.path == path
        and "POST" in route.methods
    ]


def test_application_exposes_only_the_mfa_admin_login_route():
    routes = _post_routes(app.routes, "/api/admin/login")

    assert len(routes) == 1
    assert routes[0].endpoint.__module__ == "backend.api.admin_auth"
    assert routes[0].endpoint.__name__ == "admin_session_login"


def test_legacy_admin_router_cannot_publish_password_only_login():
    assert _post_routes(legacy_admin_router.routes, "/admin/login") == []
