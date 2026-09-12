from pathlib import Path

from fastapi.routing import APIRoute

from backend.api.admin import router as admin_router
from backend.api.admin_auth import router as admin_auth_router
from backend.api.admin_promos import router as admin_promos_router
from backend.main import app


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"
ADMIN = ROOT / "api" / "admin.py"
ADMIN_AUTH = ROOT / "api" / "admin_auth.py"
ADMIN_PROMOS = ROOT / "api" / "admin_promos.py"


def _router_post_routes(router, path: str):
    return [
        route
        for route in router.routes
        if isinstance(route, APIRoute)
        and route.path == path
        and "POST" in route.methods
    ]


def test_secure_admin_login_is_the_only_application_route():
    main = MAIN.read_text(encoding="utf-8")
    monolith = ADMIN.read_text(encoding="utf-8")
    auth = ADMIN_AUTH.read_text(encoding="utf-8")

    assert '@router.post("/login", response_model=TokenOut)' not in monolith
    assert "def admin_login(" not in monolith
    assert "_REMOVED_MONOLITH_ADMIN_ROUTES" not in main
    assert '("/admin/login", "POST")' not in main
    assert 'app.include_router(admin_auth_router, prefix="/api")' in main
    assert 'app.include_router(admin_router, prefix="/api")' in main

    canonical = _router_post_routes(admin_auth_router, "/admin/login")
    assert len(canonical) == 1
    assert canonical[0].endpoint.__name__ == "admin_session_login"
    assert canonical[0].endpoint.__module__ == "backend.api.admin_auth"

    assert str(app.url_path_for("admin_session_login")) == "/api/admin/login"
    login_operation = app.openapi()["paths"]["/api/admin/login"]["post"]
    assert login_operation["operationId"].startswith("admin_session_login")

    for security_control in (
        "totp_code",
        "is_admin_ip_allowed",
        "match_stored_totp_counter",
        "consume_totp_counter",
        "create_admin_session",
        "log_admin_login",
    ):
        assert security_control in auth


def test_canonical_promo_creator_is_the_only_application_route():
    main = MAIN.read_text(encoding="utf-8")
    monolith = ADMIN.read_text(encoding="utf-8")
    promos = ADMIN_PROMOS.read_text(encoding="utf-8")

    assert '@router.post("/promocodes")' not in monolith
    assert "def admin_create_promo(" not in monolith
    assert '("/admin/promocodes", "POST")' not in main
    assert 'app.include_router(admin_promos_router, prefix="/api")' in main
    assert "normalize_promo_definition" in promos

    canonical = _router_post_routes(admin_promos_router, "/admin/promocodes")
    assert len(canonical) == 1
    assert canonical[0].endpoint.__name__ == "admin_create_promo"
    assert canonical[0].endpoint.__module__ == "backend.api.admin_promos"

    assert str(app.url_path_for("admin_create_promo")) == "/api/admin/promocodes"
    promo_operation = app.openapi()["paths"]["/api/admin/promocodes"]["post"]
    assert promo_operation["operationId"].startswith("admin_create_promo")


def test_monolith_router_does_not_register_auth_or_promo_mutations():
    registered = {
        (getattr(route, "path", ""), method)
        for route in admin_router.routes
        for method in getattr(route, "methods", set())
    }

    assert ("/admin/login", "POST") not in registered
    assert ("/admin/promocodes", "POST") not in registered
