import time
from collections import defaultdict, deque
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from ..config import get_settings


class InMemoryRateLimitMiddleware(BaseHTTPMiddleware):
    """Simple per-process rate limiter.

    Good for MVP and single container. For production multi-replica, move counters to Redis.
    """
    def __init__(self, app):
        super().__init__(app)
        self.hits = defaultdict(deque)

    async def dispatch(self, request, call_next):
        settings = get_settings()
        if not settings.rate_limit_enabled:
            return await call_next(request)

        path = request.url.path
        ip = request.client.host if request.client else "unknown"

        limit = settings.rate_limit_per_minute
        if path.startswith("/api/auth/telegram"):
            limit = settings.rate_limit_auth_per_minute
        if path.startswith("/api/admin/login"):
            limit = settings.rate_limit_admin_login_per_minute

        key = f"{ip}:{path}"
        now = time.time()
        bucket = self.hits[key]
        while bucket and now - bucket[0] > 60:
            bucket.popleft()
        if len(bucket) >= limit:
            return JSONResponse({"detail": "Rate limit exceeded"}, status_code=429)
        bucket.append(now)
        return await call_next(request)
