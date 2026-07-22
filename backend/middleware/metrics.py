import time

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

REQUEST_COUNT = Counter(
    "flashin_http_requests_total",
    "HTTP requests",
    ["method", "path", "status"],
)
REQUEST_LATENCY = Histogram(
    "flashin_http_request_latency_seconds",
    "HTTP request latency",
    ["method", "path"],
)


def route_template(request) -> str:
    """Return a bounded path label instead of user-controlled URL paths."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path or "__unmatched__"


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        started_at = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            path = route_template(request)
            REQUEST_COUNT.labels(request.method, path, "500").inc()
            REQUEST_LATENCY.labels(request.method, path).observe(
                time.perf_counter() - started_at
            )
            raise

        path = route_template(request)
        REQUEST_COUNT.labels(request.method, path, str(response.status_code)).inc()
        REQUEST_LATENCY.labels(request.method, path).observe(
            time.perf_counter() - started_at
        )
        return response


def metrics_response():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
