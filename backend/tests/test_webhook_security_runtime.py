import socket

import pytest

from backend.services.outbox import _normalize_event_type, _serialize_payload
from backend.services.webhook_security import resolve_public_webhook_addresses


def test_runtime_dns_rejects_private_resolution(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        ],
    )

    with pytest.raises(ValueError, match="private or reserved"):
        resolve_public_webhook_addresses("https://hooks.example.com/events")


def test_runtime_dns_accepts_only_public_addresses(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:2800:220:1:248:1893:25c8:1946", 443, 0, 0)),
        ],
    )

    normalized, addresses = resolve_public_webhook_addresses(
        "https://hooks.example.com/events"
    )

    assert normalized == "https://hooks.example.com/events"
    assert "93.184.216.34" in addresses
    assert "2606:2800:220:1:248:1893:25c8:1946" in addresses


def test_webhook_payload_rejects_non_json_and_nan():
    with pytest.raises(ValueError):
        _serialize_payload({"bad": object()})

    with pytest.raises(ValueError):
        _serialize_payload({"bad": float("nan")})


def test_webhook_event_type_is_strict():
    assert _normalize_event_type("order.paid") == "order.paid"

    with pytest.raises(ValueError):
        _normalize_event_type("order paid")
