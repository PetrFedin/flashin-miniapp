from backend.main import app


def _post_paths() -> set[str]:
    return {
        route.path
        for route in app.routes
        if "POST" in getattr(route, "methods", set())
    }


def test_canonical_yookassa_webhook_ingress_is_registered():
    paths = _post_paths()
    assert "/api/webhooks/yookassa" in paths


def test_legacy_yookassa_webhook_routes_remain_during_pilot_migration():
    paths = _post_paths()
    assert "/api/payments/webhook/yookassa" in paths
    assert "/api/returns/webhook/yookassa" in paths
