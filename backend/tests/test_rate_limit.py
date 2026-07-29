from collections import deque
from types import SimpleNamespace

from backend.middleware.rate_limit import (
    InMemoryRateLimitMiddleware,
    _client_ip,
    _route_bucket,
)


def _request(client_ip="10.0.0.1", headers=None):
    return SimpleNamespace(
        client=SimpleNamespace(host=client_ip),
        headers=headers or {},
    )


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
    request = _request(headers={"x-forwarded-for": "203.0.113.10"})

    assert _client_ip(request, trust_proxy_headers=False) == "10.0.0.1"


def test_last_valid_forwarded_ip_is_used_behind_isolated_proxy():
    request = _request(
        headers={"x-forwarded-for": "invalid, 198.51.100.7, 203.0.113.10"}
    )

    assert _client_ip(request, trust_proxy_headers=True) == "203.0.113.10"


def test_invalid_forwarded_chain_falls_back_to_direct_ip():
    request = _request(headers={"x-forwarded-for": "unknown, invalid"})

    assert _client_ip(request, trust_proxy_headers=True) == "10.0.0.1"


def test_cleanup_removes_expired_buckets():
    middleware = object.__new__(InMemoryRateLimitMiddleware)
    middleware.hits = {
        "expired": deque([1.0]),
        "active": deque([59.5]),
    }

    middleware._cleanup(60.0)

    assert "expired" not in middleware.hits
    assert list(middleware.hits["active"]) == [59.5]
