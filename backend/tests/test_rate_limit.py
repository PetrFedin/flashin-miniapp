import ipaddress
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


def _networks(*values):
    return tuple(ipaddress.ip_network(value) for value in values)


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

    assert _client_ip(request, trusted_hops=0) == "10.0.0.1"


def test_untrusted_direct_peer_cannot_supply_forwarded_headers():
    request = _request(
        client_ip="198.51.100.20",
        headers={"x-forwarded-for": "203.0.113.10"},
    )

    assert (
        _client_ip(
            request,
            trusted_hops=1,
            trusted_networks=_networks("10.0.0.0/8"),
        )
        == "198.51.100.20"
    )


def test_one_trusted_proxy_uses_rightmost_forwarded_address():
    request = _request(
        headers={"x-forwarded-for": "203.0.113.99, 198.51.100.7"}
    )

    assert (
        _client_ip(
            request,
            trusted_hops=1,
            trusted_networks=_networks("10.0.0.0/8"),
        )
        == "198.51.100.7"
    )


def test_two_trusted_proxy_hops_resolve_original_client():
    request = _request(
        client_ip="10.0.0.5",
        headers={"x-forwarded-for": "203.0.113.10, 10.0.0.9"},
    )

    assert (
        _client_ip(
            request,
            trusted_hops=2,
            trusted_networks=_networks("10.0.0.0/8"),
        )
        == "203.0.113.10"
    )


def test_untrusted_intermediate_proxy_fails_closed_to_direct_ip():
    request = _request(
        client_ip="10.0.0.5",
        headers={"x-forwarded-for": "203.0.113.10, 198.51.100.7"},
    )

    assert (
        _client_ip(
            request,
            trusted_hops=2,
            trusted_networks=_networks("10.0.0.0/8"),
        )
        == "10.0.0.5"
    )


def test_invalid_forwarded_chain_fails_closed_instead_of_filtering_values():
    request = _request(
        headers={"x-forwarded-for": "203.0.113.10, invalid, 198.51.100.7"}
    )

    assert (
        _client_ip(
            request,
            trusted_hops=1,
            trusted_networks=_networks("10.0.0.0/8"),
        )
        == "10.0.0.1"
    )


def test_incomplete_forwarded_chain_fails_closed():
    request = _request(headers={"x-forwarded-for": "203.0.113.10"})

    assert (
        _client_ip(
            request,
            trusted_hops=2,
            trusted_networks=_networks("10.0.0.0/8"),
        )
        == "10.0.0.1"
    )


def test_x_real_ip_is_accepted_only_for_one_trusted_proxy():
    request = _request(headers={"x-real-ip": "203.0.113.10"})
    networks = _networks("10.0.0.0/8")

    assert _client_ip(request, trusted_hops=1, trusted_networks=networks) == "203.0.113.10"
    assert _client_ip(request, trusted_hops=2, trusted_networks=networks) == "10.0.0.1"


def test_invalid_x_real_ip_falls_back_to_direct_ip():
    request = _request(headers={"x-real-ip": "not-an-ip"})

    assert (
        _client_ip(
            request,
            trusted_hops=1,
            trusted_networks=_networks("10.0.0.0/8"),
        )
        == "10.0.0.1"
    )


def test_cleanup_removes_expired_buckets():
    middleware = object.__new__(InMemoryRateLimitMiddleware)
    middleware.hits = {
        "expired": deque([1.0]),
        "active": deque([59.5]),
    }

    middleware._cleanup(60.0)

    assert "expired" not in middleware.hits
    assert list(middleware.hits["active"]) == [59.5]
