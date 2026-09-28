import asyncio
from collections import defaultdict, deque
from types import SimpleNamespace

from starlette.responses import JSONResponse

from backend.middleware.rate_limit import (
    RateLimitMiddleware,
    RateLimitBackendUnavailable,
    _client_ip,
    _rate_limit_keys,
    _route_bucket,
    _rule_for,
)


def _request(
    client_ip="10.0.0.1",
    headers=None,
    *,
    method="GET",
    path="/api/products",
):
    return SimpleNamespace(
        client=SimpleNamespace(host=client_ip),
        headers=headers or {},
        method=method,
        url=SimpleNamespace(path=path),
    )


def _memory_middleware():
    middleware = object.__new__(RateLimitMiddleware)
    middleware.hits = defaultdict(deque)
    middleware.request_count = 0
    return middleware


def test_numeric_and_uuid_route_segments_share_one_bucket():
    assert _route_bucket("/api/orders/1") == "/api/orders/:id"
    assert _route_bucket("/api/orders/999999") == "/api/orders/:id"
    assert (
        _route_bucket("/api/resources/550e8400-e29b-41d4-a716-446655440000")
        == "/api/resources/:id"
    )


def test_static_routes_are_not_over_normalized():
    assert _route_bucket("/api/auth/telegram") == "/api/auth/telegram"
    assert _route_bucket("/api/admin/login") == "/api/admin/login"


def test_proxy_headers_are_ignored_when_not_trusted():
    request = _request(
        headers={
            "x-forwarded-for": "203.0.113.10",
            "x-real-ip": "198.51.100.7",
        }
    )
    assert _client_ip(request, trust_proxy_headers=False) == "10.0.0.1"


def test_single_valid_forwarded_ip_is_used_behind_isolated_proxy():
    request = _request(headers={"x-forwarded-for": "203.0.113.10"})
    assert _client_ip(request, trust_proxy_headers=True) == "203.0.113.10"


def test_forwarded_chain_fails_closed_to_direct_peer():
    request = _request(headers={"x-forwarded-for": "198.51.100.7, 203.0.113.10"})
    assert _client_ip(request, trust_proxy_headers=True) == "10.0.0.1"


def test_invalid_forwarded_value_falls_back_to_direct_ip():
    request = _request(headers={"x-forwarded-for": "not-an-ip"})
    assert _client_ip(request, trust_proxy_headers=True) == "10.0.0.1"


def test_x_real_ip_is_never_an_application_client_identity_fallback():
    request = _request(headers={"x-real-ip": "203.0.113.10"})
    assert _client_ip(request, trust_proxy_headers=True) == "10.0.0.1"


def test_route_policy_has_dedicated_sensitive_and_availability_classes():
    cases = {
        ("POST", "/api/auth/telegram"): ("auth", True),
        ("POST", "/api/admin/login"): ("admin_auth", True),
        ("POST", "/api/orders/checkout"): ("checkout", True),
        ("POST", "/api/payments"): ("payment", True),
        ("POST", "/api/returns"): ("return", True),
        ("POST", "/api/webhooks/yookassa"): ("webhook", True),
        ("POST", "/api/support/tickets"): ("support", False),
        ("GET", "/api/search/products"): ("search", False),
        ("GET", "/api/products"): ("general", False),
    }
    for (method, route), expected in cases.items():
        rule = _rule_for(method, route)
        assert (rule.category, rule.fail_closed) == expected


def test_sensitive_authenticated_routes_bind_ip_session_and_checkout_operation_without_raw_secrets():
    request = _request(
        method="POST",
        path="/api/orders/checkout",
        headers={
            "authorization": "Bearer raw-customer-token",
            "idempotency-key": "checkout-operation-123456",
        },
    )
    rule = _rule_for(request.method, request.url.path)
    keys = _rate_limit_keys(request, rule, request.url.path, "203.0.113.10")

    assert len(keys) == 3
    assert any(":ip:" in key for key in keys)
    assert any(":session:" in key for key in keys)
    assert any(":operation:" in key for key in keys)
    joined = "|".join(keys)
    assert "raw-customer-token" not in joined
    assert "checkout-operation-123456" not in joined
    assert "203.0.113.10" not in joined


def test_missing_bearer_cannot_remove_ip_budget():
    request = _request(method="POST", path="/api/payments")
    rule = _rule_for(request.method, request.url.path)
    keys = _rate_limit_keys(request, rule, request.url.path, "203.0.113.10")

    assert len(keys) == 1
    assert ":ip:" in keys[0]


def test_local_multi_key_decision_is_atomic_when_one_scope_is_exhausted():
    middleware = _memory_middleware()
    first = middleware._memory_hit(("ip", "session-a"), 1)
    blocked = middleware._memory_hit(("ip", "session-b"), 1)

    assert first.allowed is True
    assert blocked.allowed is False
    assert len(middleware.hits["session-b"]) == 0


def test_cleanup_removes_only_buckets_outside_the_full_window():
    middleware = _memory_middleware()
    middleware.hits = defaultdict(
        deque,
        {
            "expired": deque([0.0]),
            "active": deque([59.5]),
        },
    )

    middleware._cleanup(60.0)

    assert "expired" not in middleware.hits
    assert list(middleware.hits["active"]) == [59.5]

class _UnavailableLimiter:
    async def hit(self, _keys, _limit):
        raise RateLimitBackendUnavailable("redis unavailable")


def _redis_settings(**overrides):
    values = {
        "rate_limit_enabled": True,
        "rate_limit_backend": "redis",
        "rate_limit_redis_url": "redis://redis:6379/0",
        "rate_limit_redis_connect_timeout_seconds": 1.0,
        "rate_limit_redis_socket_timeout_seconds": 1.0,
        "rate_limit_per_minute": 120,
        "rate_limit_auth_per_minute": 20,
        "rate_limit_admin_login_per_minute": 10,
        "rate_limit_search_per_minute": 120,
        "rate_limit_checkout_per_minute": 12,
        "rate_limit_payment_per_minute": 12,
        "rate_limit_return_per_minute": 12,
        "rate_limit_support_per_minute": 30,
        "rate_limit_webhook_per_minute": 180,
        "app_env": "production",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_sensitive_route_fails_closed_when_shared_backend_is_unavailable(monkeypatch):
    from backend.middleware import rate_limit as module

    middleware = _memory_middleware()
    middleware._distributed = None
    middleware._distributed_url = ""
    monkeypatch.setattr(module, "get_settings", lambda: _redis_settings())
    monkeypatch.setattr(
        middleware,
        "_distributed_limiter",
        lambda _settings: _UnavailableLimiter(),
    )
    downstream_called = False

    async def downstream(_request):
        nonlocal downstream_called
        downstream_called = True
        return JSONResponse({"ok": True})

    request = _request(
        client_ip="203.0.113.10",
        method="POST",
        path="/api/orders/checkout",
        headers={"idempotency-key": "checkout-operation-123456"},
    )
    response = asyncio.run(middleware.dispatch(request, downstream))

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.headers["x-ratelimit-policy"] == "checkout"
    assert downstream_called is False


def test_availability_route_fails_open_with_explicit_degraded_header(monkeypatch):
    from backend.middleware import rate_limit as module

    middleware = _memory_middleware()
    middleware._distributed = None
    middleware._distributed_url = ""
    monkeypatch.setattr(module, "get_settings", lambda: _redis_settings())
    monkeypatch.setattr(
        middleware,
        "_distributed_limiter",
        lambda _settings: _UnavailableLimiter(),
    )
    downstream_called = False

    async def downstream(_request):
        nonlocal downstream_called
        downstream_called = True
        return JSONResponse({"ok": True})

    request = _request(
        client_ip="203.0.113.10",
        method="GET",
        path="/api/search/products",
    )
    response = asyncio.run(middleware.dispatch(request, downstream))

    assert response.status_code == 200
    assert response.headers["x-ratelimit-policy"] == "search"
    assert response.headers["x-ratelimit-degraded"] == "open"
    assert downstream_called is True

