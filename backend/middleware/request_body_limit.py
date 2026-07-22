from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .request_context import REQUEST_ID_HEADER


class RequestBodyTooLarge(Exception):
    """Raised when a streamed request body exceeds the configured limit."""


class RequestBodyLimitMiddleware:
    """Reject oversized HTTP request bodies before they exhaust memory.

    A valid Content-Length value is rejected immediately. The receive wrapper
    enforces the same limit while streaming, so chunked requests and requests
    without Content-Length cannot bypass the limit.
    """

    def __init__(self, app: ASGIApp, max_body_bytes: int) -> None:
        if max_body_bytes <= 0:
            raise ValueError("max_body_bytes must be greater than zero")
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(scope.get("state", {}).get("request_id", ""))
        content_length = self._content_length(scope)
        if content_length is not None and content_length > self.max_body_bytes:
            await self._send_too_large(scope, receive, send, request_id)
            return

        received_bytes = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.max_body_bytes:
                    raise RequestBodyTooLarge
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except RequestBodyTooLarge:
            if response_started:
                raise
            await self._send_too_large(scope, receive, send, request_id)

    @staticmethod
    def _content_length(scope: Scope) -> int | None:
        for name, value in scope.get("headers", []):
            if name != b"content-length":
                continue
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                return None
            return parsed if parsed >= 0 else None
        return None

    @staticmethod
    async def _send_too_large(
        scope: Scope,
        receive: Receive,
        send: Send,
        request_id: str,
    ) -> None:
        headers = {"Cache-Control": "no-store"}
        if request_id:
            headers[REQUEST_ID_HEADER] = request_id

        response = JSONResponse(
            status_code=413,
            content={
                "detail": "Request body is too large",
                "request_id": request_id,
            },
            headers=headers,
        )
        await response(scope, receive, send)
