import json

from starlette.responses import JSONResponse

from ..order_statuses import PROVIDER_OWNED_ORDER_STATUSES


class AdminOrderStateGuardMiddleware:
    """Prevent generic admin edits from bypassing dedicated order workflows."""

    _MAX_BODY_BYTES = 64 * 1024

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if not self._is_guarded_request(scope):
            await self.app(scope, receive, send)
            return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":
                await self.app(scope, self._replay(bytes(body)), send)
                return
            body.extend(message.get("body", b""))
            if len(body) > self._MAX_BODY_BYTES:
                response = JSONResponse(
                    status_code=413,
                    content={"detail": "Request body is too large"},
                )
                await response(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        raw_body = bytes(body)
        try:
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            await self.app(scope, self._replay(raw_body), send)
            return

        requested_status = ""
        if isinstance(payload, dict):
            requested_status = str(payload.get("status") or "").strip().lower()

        if requested_status in PROVIDER_OWNED_ORDER_STATUSES:
            response = JSONResponse(
                status_code=409,
                content={
                    "detail": (
                        f"Status {requested_status} is controlled by a dedicated payment, refund, or cancellation workflow"
                    )
                },
            )
            await response(scope, receive, send)
            return

        await self.app(scope, self._replay(raw_body), send)

    @staticmethod
    def _is_guarded_request(scope: dict) -> bool:
        if scope.get("type") != "http":
            return False
        if str(scope.get("method") or "").upper() != "PATCH":
            return False
        path = str(scope.get("path") or "")
        prefix = "/api/admin/orders/"
        if not path.startswith(prefix):
            return False
        order_id = path[len(prefix):].strip("/")
        return order_id.isdigit()

    @staticmethod
    def _replay(body: bytes):
        sent = False

        async def receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        return receive
