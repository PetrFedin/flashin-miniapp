import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from backend.services import http_clients, payments


class FakeClient:
    def __init__(self):
        self.is_closed = False
        self.close_calls = 0
        self.requests = []
        self.responses = []

    async def aclose(self):
        self.close_calls += 1
        self.is_closed = True

    async def request(self, method, path, **kwargs):
        self.requests.append((method, path, kwargs))
        if self.responses:
            return self.responses.pop(0)
        return httpx.Response(
            200,
            json={"ok": True},
            request=httpx.Request(method, f"https://api.yookassa.ru/v3{path}"),
        )


def _reset_clients():
    asyncio.run(http_clients.close_http_clients())


def test_shared_client_is_started_once_and_closed_once(monkeypatch):
    _reset_clients()
    fake = FakeClient()
    monkeypatch.setattr(http_clients, "_new_yookassa_client", lambda: fake)

    http_clients.start_http_clients()
    http_clients.start_http_clients()

    assert http_clients.http_client_state() == {
        "yookassa_configured": True,
        "yookassa_open": True,
    }
    asyncio.run(http_clients.close_http_clients())
    assert fake.close_calls == 1
    assert http_clients.http_client_state() == {
        "yookassa_configured": False,
        "yookassa_open": False,
    }


def test_context_uses_and_closes_temporary_client_without_lifespan(monkeypatch):
    _reset_clients()
    fake = FakeClient()
    monkeypatch.setattr(http_clients, "_new_yookassa_client", lambda: fake)

    async def run():
        async with http_clients.yookassa_client() as client:
            assert client is fake
            assert client.is_closed is False

    asyncio.run(run())
    assert fake.close_calls == 1
    assert fake.is_closed is True


def test_context_reuses_shared_client_without_closing_it(monkeypatch):
    _reset_clients()
    fake = FakeClient()
    monkeypatch.setattr(http_clients, "_new_yookassa_client", lambda: fake)
    http_clients.start_http_clients()

    async def run():
        async with http_clients.yookassa_client() as first:
            async with http_clients.yookassa_client() as second:
                assert first is fake
                assert second is fake

    asyncio.run(run())
    assert fake.close_calls == 0
    asyncio.run(http_clients.close_http_clients())
    assert fake.close_calls == 1


def test_yookassa_request_retries_through_same_managed_client(monkeypatch):
    fake = FakeClient()
    fake.responses = [
        httpx.Response(
            503,
            headers={"Retry-After": "0"},
            request=httpx.Request("GET", "https://api.yookassa.ru/v3/payments/pay-1"),
        ),
        httpx.Response(
            200,
            json={"id": "pay-1", "status": "succeeded"},
            request=httpx.Request("GET", "https://api.yookassa.ru/v3/payments/pay-1"),
        ),
    ]

    @asynccontextmanager
    async def fake_context():
        yield fake

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(payments, "yookassa_client", fake_context)
    monkeypatch.setattr(payments, "get_settings", lambda: SimpleNamespace(
        yookassa_shop_id="shop",
        yookassa_secret_key="secret",
    ))
    monkeypatch.setattr(payments.asyncio, "sleep", no_sleep)

    result = asyncio.run(payments._request_yookassa("GET", "/payments/pay-1"))

    assert result == {"id": "pay-1", "status": "succeeded"}
    assert len(fake.requests) == 2
    assert {id(fake) for _ in fake.requests} == {id(fake)}
    for method, path, kwargs in fake.requests:
        assert method == "GET"
        assert path == "/payments/pay-1"
        assert kwargs["auth"] == ("shop", "secret")


def test_closed_shared_client_falls_back_to_temporary(monkeypatch):
    _reset_clients()
    closed = FakeClient()
    closed.is_closed = True
    temporary = FakeClient()
    http_clients._yookassa_client = closed
    monkeypatch.setattr(http_clients, "_new_yookassa_client", lambda: temporary)

    async def run():
        async with http_clients.yookassa_client() as client:
            assert client is temporary

    asyncio.run(run())
    assert temporary.close_calls == 1
    http_clients._yookassa_client = None
