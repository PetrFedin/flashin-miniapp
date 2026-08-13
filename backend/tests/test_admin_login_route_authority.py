from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"
ADMIN = ROOT / "api" / "admin.py"
ADMIN_AUTH = ROOT / "api" / "admin_auth.py"


def test_secure_admin_login_is_the_authoritative_application_route():
    main = MAIN.read_text(encoding="utf-8")
    auth = ADMIN_AUTH.read_text(encoding="utf-8")

    assert '("/admin/login", "POST")' in main
    assert "admin_router.routes[:]" in main
    assert 'app.include_router(admin_auth_router, prefix="/api")' in main
    assert 'app.include_router(admin_router, prefix="/api")' in main
    assert main.index('app.include_router(admin_auth_router, prefix="/api")') < main.index(
        'app.include_router(admin_router, prefix="/api")'
    )

    for security_control in (
        "totp_code",
        "is_admin_ip_allowed",
        "verify_stored_totp",
        "create_admin_session",
        "log_admin_login",
    ):
        assert security_control in auth


def test_legacy_monolith_login_cannot_become_authoritative_by_router_order_drift():
    main = MAIN.read_text(encoding="utf-8")
    monolith = ADMIN.read_text(encoding="utf-8")

    assert '@router.post("/login", response_model=TokenOut)' in monolith
    removal_block = main.split("_REMOVED_MONOLITH_ADMIN_ROUTES", 1)[1].split(
        "if settings.sentry_dsn", 1
    )[0]
    assert '("/admin/login", "POST")' in removal_block
    assert "admin_router.routes[:]" in removal_block
    assert 'getattr(route, "path", "") == path' in removal_block
    assert 'method in getattr(route, "methods", set())' in removal_block
