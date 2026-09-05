from __future__ import annotations

import re
from uuid import uuid4

import sentry_sdk
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_HEADER_NAME = b"x-request-id"
_HEADER_TEXT = "X-Request-ID"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def normalize_request_id(value: str | None) -> str:
    """Accept a bounded safe caller correlation id or generate a server id."""

    candidate = str(value or "").strip()
    if candidate and _REQUEST_ID_RE.fullmatch(candidate):
        return candidate
    return uuid4().hex


def _header_request_id(scope: Scope) -> str | None:
    for raw_name, raw_value in scope.get("headers", []):
        if raw_name.lower() == _HEADER_NAME:
            try:
                return raw_value.decode("ascii")
            except UnicodeDecodeError:
                return None
    return None


class RequestIdMiddleware:
    """Attach one safe correlation id to every HTTP request and response.

    The value is intentionally non-secret and bounded. Downstream code can read it
    from ``scope['state']['request_id']`` / ``request.state.request_id``. Invalid or
    oversized caller values are never reflected back.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        request_id = normalize_request_id(_header_request_id(scope))
        state = scope.setdefault("state", {})
        state["request_id"] = request_id

        # Sentry SDK scopes are context-local under ASGI. Only attach the generated
        # non-PII correlation id; never attach request headers or payloads here.
        sentry_sdk.set_tag("request_id", request_id)

        async def send_with_request_id(message: Message) -> None:
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers", []))
                headers = [
                    (name, value)
                    for name, value in headers
                    if name.lower() != _HEADER_NAME
                ]
                headers.append((_HEADER_NAME, request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_request_id)


__all__ = ["RequestIdMiddleware", "normalize_request_id", "_HEADER_TEXT"]
