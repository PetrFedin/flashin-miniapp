from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx

_YOOKASSA_BASE_URL = "https://api.yookassa.ru/v3"
_YOOKASSA_TIMEOUT = httpx.Timeout(20.0, connect=5.0)
_YOOKASSA_LIMITS = httpx.Limits(
    max_connections=50,
    max_keepalive_connections=20,
    keepalive_expiry=30.0,
)
_yookassa_client: httpx.AsyncClient | None = None


def _new_yookassa_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=_YOOKASSA_BASE_URL,
        timeout=_YOOKASSA_TIMEOUT,
        limits=_YOOKASSA_LIMITS,
        headers={"User-Agent": "flashin-miniapp-backend/1"},
        follow_redirects=False,
    )


def start_http_clients() -> None:
    global _yookassa_client
    if _yookassa_client is not None and not _yookassa_client.is_closed:
        return
    _yookassa_client = _new_yookassa_client()


async def close_http_clients() -> None:
    global _yookassa_client
    client = _yookassa_client
    _yookassa_client = None
    if client is not None and not client.is_closed:
        await client.aclose()


def http_client_state() -> dict[str, bool]:
    client = _yookassa_client
    return {
        "yookassa_configured": client is not None,
        "yookassa_open": client is not None and not client.is_closed,
    }


@asynccontextmanager
async def yookassa_client() -> AsyncIterator[httpx.AsyncClient]:
    shared = _yookassa_client
    if shared is not None and not shared.is_closed:
        yield shared
        return

    temporary = _new_yookassa_client()
    try:
        yield temporary
    finally:
        await temporary.aclose()
