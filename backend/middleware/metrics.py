import time

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

REQUEST_COUNT = Counter(
    "flashin_http_requests_total",
    "HTTP requests",
    ["method", "route", "status"],
)
REQUEST_LATENCY = Histogram(
    "flashin_http_request_latency_seconds",
    "HTTP request latency",
    ["method", "route"],
)


def route_template(request) -> str:
    """Return a bounded route label instead of user-controlled URL paths."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path or "__unmatched__"


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        started_at = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            route = route_template(request)
            REQUEST_COUNT.labels(request.method, route, "500").inc()
            REQUEST_LATENCY.labels(request.method, route).observe(
                time.perf_counter() - started_at
            )
            raise

        route = route_template(request)
        REQUEST_COUNT.labels(request.method, route, str(response.status_code)).inc()
        REQUEST_LATENCY.labels(request.method, route).observe(
            time.perf_counter() - started_at
        )
        return response


def metrics_response():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
