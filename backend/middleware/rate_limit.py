import ipaddress
import re
import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from ..config import get_settings

_WINDOW_SECONDS = 60.0
_CLEANUP_INTERVAL_REQUESTS = 500
_MAX_BUCKETS = 50_000
_ID_SEGMENT = re.compile(r"^(?:\d+|[0-9a-fA-F]{8,}|[0-9a-fA-F-]{32,})$")
_EXEMPT_PATHS = {"/health", "/ready"}
_ADMIN_AUTH_ROUTES = {
    "/api/admin/login",
    "/api/admin/password-reset/confirm",
}


def _valid_ip(value: str) -> str | None:
    candidate = (value or "").strip()
    if not candidate:
        return None
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def _client_ip(request, *, trust_proxy_headers: bool) -> str:
    direct_ip = _valid_ip(request.client.host if request.client else "") or "unknown"
    if not trust_proxy_headers:
        return direct_ip

    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        valid_chain = [ip for raw in forwarded.split(",") if (ip := _valid_ip(raw))]
        if valid_chain:
            return valid_chain[-1]

    real_ip = _valid_ip(request.headers.get("x-real-ip", ""))
    return real_ip or direct_ip


def _route_bucket(path: str) -> str:
    normalized = "/" + "/".join(segment for segment in path.split("/") if segment)
    if normalized == "/":
        return normalized

    segments = normalized.split("/")
    return "/".join(":id" if _ID_SEGMENT.fullmatch(segment) else segment for segment in segments)


class InMemoryRateLimitMiddleware(BaseHTTPMiddleware):
    """Per-process sliding-window limiter with bounded memory.

    Production traffic is expected to reach the backend only through the
    isolated reverse proxy. In production, the proxy-provided client IP is
    therefore used; development keeps direct socket addressing to prevent
    header spoofing.
    """

    def __init__(self, app):
        super().__init__(app)
        self.hits: dict[str, deque[float]] = defaultdict(deque)
        self.request_count = 0

    async def dispatch(self, request, call_next):
        settings = get_settings()
        if not settings.rate_limit_enabled:
            return await call_next(request)

        path = request.url.path
        if request.method.upper() == "OPTIONS" or path in _EXEMPT_PATHS:
            return await call_next(request)

        route = _route_bucket(path)
        limit = settings.rate_limit_per_minute
        category = "general"
        if route == "/api/auth/telegram":
            limit = settings.rate_limit_auth_per_minute
            category = "auth"
        elif route in _ADMIN_AUTH_ROUTES:
            limit = settings.rate_limit_admin_login_per_minute
            category = "admin_auth"

        trust_proxy_headers = settings.app_env.strip().lower() == "production"
        client_ip = _client_ip(request, trust_proxy_headers=trust_proxy_headers)
        key = f"{category}:{client_ip}:{request.method.upper()}:{route}"

        now = time.monotonic()
        bucket = self.hits[key]
        self._expire(bucket, now)
        remaining = max(limit - len(bucket), 0)

        if len(bucket) >= limit:
            retry_after = max(int(_WINDOW_SECONDS - (now - bucket[0])) + 1, 1)
            return JSONResponse(
                {"detail": "Rate limit exceeded"},
                status_code=429,
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

        bucket.append(now)
        self.request_count += 1
        if self.request_count % _CLEANUP_INTERVAL_REQUESTS == 0 or len(self.hits) > _MAX_BUCKETS:
            self._cleanup(now)

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(remaining - 1, 0))
        return response

    @staticmethod
    def _expire(bucket: deque[float], now: float) -> None:
        while bucket and now - bucket[0] >= _WINDOW_SECONDS:
            bucket.popleft()

    def _cleanup(self, now: float) -> None:
        stale_keys: list[str] = []
        for key, bucket in self.hits.items():
            self._expire(bucket, now)
            if not bucket:
                stale_keys.append(key)
        for key in stale_keys:
            self.hits.pop(key, None)

        if len(self.hits) <= _MAX_BUCKETS:
            return

        oldest = sorted(
            self.hits.items(),
            key=lambda item: item[1][-1] if item[1] else float("-inf"),
        )
        for key, _ in oldest[: len(self.hits) - _MAX_BUCKETS]:
            self.hits.pop(key, None)
