from __future__ import annotations

import hashlib
import ipaddress
import math
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from ..config import get_settings
from ..services.distributed_rate_limit import (
    DistributedRateLimiter,
    RateLimitBackendUnavailable,
    RateLimitDecision,
)
from .metrics import record_rate_limit_event, set_rate_limit_backend_available


_WINDOW_SECONDS = 60.0
_CLEANUP_INTERVAL_REQUESTS = 500
_MAX_BUCKETS = 50_000
_ID_SEGMENT = re.compile(r"^(?:\d+|[0-9a-fA-F]{8,}|[0-9a-fA-F-]{32,})$")
_EXEMPT_PATHS = {"/health", "/ready"}
_ADMIN_AUTH_ROUTES = {
    "/api/admin/login",
    "/api/admin/password-reset/confirm",
}
_WEBHOOK_ROUTES = {
    "/api/payments/webhook/yookassa",
    "/api/returns/webhook/yookassa",
    "/api/webhooks/yookassa",
}


@dataclass(frozen=True)
class RateLimitRule:
    category: str
    setting: str
    fail_closed: bool
    session_scope: bool = False
    operation_scope: bool = False


def _valid_ip(value: str) -> str | None:
    candidate = (value or "").strip()
    if not candidate:
        return None
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def _client_ip(request, *, trust_proxy_headers: bool) -> str:
    """Resolve the client IP using the isolated single-Caddy-hop contract."""
    direct_ip = _valid_ip(request.client.host if request.client else "") or "unknown"
    if not trust_proxy_headers:
        return direct_ip

    forwarded = request.headers.get("x-forwarded-for", "")
    parts = [part.strip() for part in forwarded.split(",") if part.strip()]
    if len(parts) != 1:
        return direct_ip
    return _valid_ip(parts[0]) or direct_ip


def _route_bucket(path: str) -> str:
    normalized = "/" + "/".join(segment for segment in path.split("/") if segment)
    if normalized == "/":
        return normalized
    segments = normalized.split("/")
    return "/".join(":id" if _ID_SEGMENT.fullmatch(segment) else segment for segment in segments)


def _rule_for(method: str, route: str) -> RateLimitRule:
    normalized_method = method.upper()
    if route == "/api/auth/telegram":
        return RateLimitRule("auth", "rate_limit_auth_per_minute", True)
    if route in _ADMIN_AUTH_ROUTES:
        return RateLimitRule("admin_auth", "rate_limit_admin_login_per_minute", True)
    if route in _WEBHOOK_ROUTES:
        return RateLimitRule("webhook", "rate_limit_webhook_per_minute", True)
    if normalized_method == "POST" and route == "/api/orders/checkout":
        return RateLimitRule(
            "checkout",
            "rate_limit_checkout_per_minute",
            True,
            session_scope=True,
            operation_scope=True,
        )
    if normalized_method == "POST" and route == "/api/payments":
        return RateLimitRule(
            "payment",
            "rate_limit_payment_per_minute",
            True,
            session_scope=True,
        )
    if normalized_method == "POST" and route.startswith("/api/returns"):
        return RateLimitRule(
            "return",
            "rate_limit_return_per_minute",
            True,
            session_scope=True,
        )
    if normalized_method == "POST" and route == "/api/support/tickets":
        return RateLimitRule(
            "support",
            "rate_limit_support_per_minute",
            False,
            session_scope=True,
        )
    if normalized_method == "GET" and route == "/api/search/products":
        return RateLimitRule("search", "rate_limit_search_per_minute", False)
    return RateLimitRule("general", "rate_limit_per_minute", False)


def _identity_fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _bearer_token(request) -> str:
    header = str(request.headers.get("authorization", "") or "").strip()
    if not header.lower().startswith("bearer "):
        return ""
    return header[7:].strip()


def _rate_limit_keys(request, rule: RateLimitRule, route: str, client_ip: str) -> tuple[str, ...]:
    method = request.method.upper()
    identities: list[tuple[str, str]] = [("ip", _identity_fingerprint(client_ip))]
    if rule.session_scope:
        token = _bearer_token(request)
        if token:
            identities.append(("session", _identity_fingerprint(token)))
    if rule.operation_scope:
        operation = str(request.headers.get("idempotency-key", "") or "").strip()
        if operation:
            identities.append(("operation", _identity_fingerprint(operation)))
    return tuple(
        f"flashin:rate-limit:v1:{rule.category}:{method}:{route}:{scope}:{identity}"
        for scope, identity in identities
    )


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Shared production limiter with an explicit bounded local-development mode."""

    def __init__(self, app):
        super().__init__(app)
        self.hits: dict[str, deque[float]] = defaultdict(deque)
        self.request_count = 0
        self._distributed: DistributedRateLimiter | None = None
        self._distributed_url = ""

    def _distributed_limiter(self, settings) -> DistributedRateLimiter:
        url = str(settings.rate_limit_redis_url or "").strip()
        if self._distributed is None or self._distributed_url != url:
            self._distributed = DistributedRateLimiter(
                url,
                connect_timeout_seconds=float(settings.rate_limit_redis_connect_timeout_seconds),
                socket_timeout_seconds=float(settings.rate_limit_redis_socket_timeout_seconds),
            )
            self._distributed_url = url
        return self._distributed

    async def dispatch(self, request, call_next):
        settings = get_settings()
        if not settings.rate_limit_enabled:
            return await call_next(request)

        path = request.url.path
        if request.method.upper() == "OPTIONS" or path in _EXEMPT_PATHS:
            return await call_next(request)

        route = _route_bucket(path)
        rule = _rule_for(request.method, route)
        limit = int(getattr(settings, rule.setting))
        trust_proxy_headers = settings.app_env.strip().lower() == "production"
        client_ip = _client_ip(request, trust_proxy_headers=trust_proxy_headers)
        keys = _rate_limit_keys(request, rule, route, client_ip)

        backend = str(settings.rate_limit_backend or "").strip().lower()
        degraded_open = False
        if backend == "redis":
            try:
                decision = await self._distributed_limiter(settings).hit(keys, limit)
                set_rate_limit_backend_available(True)
            except RateLimitBackendUnavailable:
                set_rate_limit_backend_available(False)
                if rule.fail_closed:
                    record_rate_limit_event(rule.category, "backend_fail_closed")
                    return JSONResponse(
                        {"detail": "Rate limit service unavailable"},
                        status_code=503,
                        headers={
                            "Retry-After": "1",
                            "X-RateLimit-Policy": rule.category,
                        },
                    )
                record_rate_limit_event(rule.category, "backend_fail_open")
                degraded_open = True
                decision = RateLimitDecision(allowed=True, remaining=limit)
        else:
            set_rate_limit_backend_available(True)
            decision = self._memory_hit(keys, limit)

        if not decision.allowed:
            record_rate_limit_event(rule.category, "rejected")
            return JSONResponse(
                {"detail": "Rate limit exceeded"},
                status_code=429,
                headers={
                    "Retry-After": str(max(decision.retry_after_seconds, 1)),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Policy": rule.category,
                },
            )

        record_rate_limit_event(rule.category, "allowed")
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(decision.remaining, 0))
        response.headers["X-RateLimit-Policy"] = rule.category
        if degraded_open:
            response.headers["X-RateLimit-Degraded"] = "open"
        return response

    def _memory_hit(self, keys: tuple[str, ...], limit: int) -> RateLimitDecision:
        now = time.monotonic()
        unique_keys = tuple(dict.fromkeys(keys))
        retry_after = 0
        remaining = limit

        for key in unique_keys:
            bucket = self.hits[key]
            self._expire(bucket, now)
            remaining = min(remaining, max(limit - len(bucket), 0))
            if len(bucket) >= limit:
                retry_after = max(
                    retry_after,
                    max(math.ceil(_WINDOW_SECONDS - (now - bucket[0])), 1),
                )

        if retry_after:
            return RateLimitDecision(
                allowed=False,
                remaining=0,
                retry_after_seconds=retry_after,
            )

        for key in unique_keys:
            self.hits[key].append(now)

        self.request_count += 1
        if self.request_count % _CLEANUP_INTERVAL_REQUESTS == 0 or len(self.hits) > _MAX_BUCKETS:
            self._cleanup(now)

        return RateLimitDecision(allowed=True, remaining=max(remaining - 1, 0))

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


# Import compatibility for older internal tests and release archives. Production
# behavior is selected by RATE_LIMIT_BACKEND and is forced to Redis by config.
InMemoryRateLimitMiddleware = RateLimitMiddleware
