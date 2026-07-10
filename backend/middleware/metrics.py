import time
from starlette.middleware.base import BaseHTTPMiddleware
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response

REQUEST_COUNT = Counter("flashin_http_requests_total", "HTTP requests", ["method", "path", "status"])
REQUEST_LATENCY = Histogram("flashin_http_request_latency_seconds", "HTTP request latency", ["method", "path"])


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        start = time.time()
        response = await call_next(request)
        path = request.url.path
        REQUEST_COUNT.labels(request.method, path, str(response.status_code)).inc()
        REQUEST_LATENCY.labels(request.method, path).observe(time.time() - start)
        return response


def metrics_response():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
