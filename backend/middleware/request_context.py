import logging
import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware

REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

access_logger = logging.getLogger("backend.access")


def _resolve_request_id(value: str | None) -> str:
    """Accept safe upstream correlation IDs and replace malformed values."""
    if value and _REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return uuid.uuid4().hex


def _route_name(request) -> str:
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    return route_path or request.url.path


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a safe correlation ID and emit one structured access-log event."""

    async def dispatch(self, request, call_next):
        request_id = _resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id
        started_at = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - started_at) * 1000
            access_logger.exception(
                "request_failed",
                extra={
                    "request_id": request_id,
                    "http_method": request.method,
                    "http_route": _route_name(request),
                    "duration_ms": round(duration_ms, 2),
                },
            )
            raise

        duration_ms = (time.perf_counter() - started_at) * 1000
        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers.setdefault("Server-Timing", f"app;dur={duration_ms:.2f}")

        access_logger.info(
            "request_completed",
            extra={
                "request_id": request_id,
                "http_method": request.method,
                "http_route": _route_name(request),
                "status_code": response.status_code,
                "duration_ms": round(duration_ms, 2),
            },
        )
        return response
