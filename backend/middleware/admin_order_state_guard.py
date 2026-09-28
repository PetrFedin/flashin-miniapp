import json

from starlette.responses import JSONResponse


class AdminOrderStateGuardMiddleware:
    """Fence the legacy Admin order PATCH before route-level workflow checks."""

    _MAX_BODY_BYTES = 64 * 1024
    _ALLOWED_COMPATIBILITY_STATUS = "assembling"

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

        if isinstance(payload, dict):
            delivery_status = str(payload.get("delivery_status") or "").strip()
            tracking_number = str(payload.get("tracking_number") or "").strip()
            requested_status = str(payload.get("status") or "").strip().lower()

            if delivery_status or tracking_number:
                response = JSONResponse(
                    status_code=409,
                    content={
                        "detail": (
                            "Delivery status and tracking are controlled by the dedicated shipment workflow"
                        )
                    },
                )
                await response(scope, receive, send)
                return

            if requested_status and requested_status != self._ALLOWED_COMPATIBILITY_STATUS:
                response = JSONResponse(
                    status_code=409,
                    content={
                        "detail": (
                            "Generic order PATCH may only start paid-order fulfillment with status=assembling"
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
