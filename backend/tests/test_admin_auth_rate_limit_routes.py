from backend.middleware.rate_limit import _ADMIN_AUTH_ROUTES


def test_sensitive_admin_auth_routes_use_the_strict_limit_bucket():
    assert _ADMIN_AUTH_ROUTES == {
        "/api/admin/login",
        "/api/admin/password-reset/confirm",
        "/api/admin/mfa/setup/start",
        "/api/admin/mfa/setup/confirm",
    }
