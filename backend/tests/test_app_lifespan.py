import asyncio

import pytest

from backend import main


def test_lifespan_starts_bootstrap_and_closes_resources(monkeypatch):
    events = []

    monkeypatch.setattr(main, "start_http_clients", lambda: events.append("http_start"))
    monkeypatch.setattr(main, "on_startup", lambda: events.append("db_start"))

    async def close():
        events.append("http_close")

    monkeypatch.setattr(main, "close_http_clients", close)

    async def run():
        async with main.lifespan(main.app):
            events.append("serve")

    asyncio.run(run())
    assert events == ["http_start", "db_start", "serve", "http_close"]


def test_lifespan_closes_http_client_when_bootstrap_fails(monkeypatch):
    events = []

    monkeypatch.setattr(main, "start_http_clients", lambda: events.append("http_start"))

    def fail_startup():
        events.append("db_start")
        raise RuntimeError("bootstrap failed")

    monkeypatch.setattr(main, "on_startup", fail_startup)

    async def close():
        events.append("http_close")

    monkeypatch.setattr(main, "close_http_clients", close)

    async def run():
        async with main.lifespan(main.app):
            pass

    with pytest.raises(RuntimeError, match="bootstrap failed"):
        asyncio.run(run())
    assert events == ["http_start", "db_start", "http_close"]


def test_fastapi_application_uses_lifespan_not_deprecated_event_hooks():
    # FastAPI composes the application lifespan with router lifespans, so the
    # installed callable is intentionally a wrapper rather than the same object.
    assert callable(main.app.router.lifespan_context)
    assert not main.app.router.on_startup
    assert not main.app.router.on_shutdown
