import ipaddress
import re
import time
from collections import defaultdict, deque
from collections.abc import Iterable

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

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


def _parse_ip(value: str) -> IPAddress | None:
    candidate = (value or "").strip()
    if not candidate:
        return None
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        return None


def _valid_ip(value: str) -> str | None:
    parsed = _parse_ip(value)
    return str(parsed) if parsed is not None else None


def _ip_in_networks(ip: IPAddress, networks: Iterable[IPNetwork]) -> bool:
    return any(ip.version == network.version and ip in network for network in networks)


def _parse_forwarded_chain(value: str) -> list[IPAddress] | None:
    raw_values = value.split(",")
    if not raw_values or any(not raw.strip() for raw in raw_values):
        return None

    chain: list[IPAddress] = []
    for raw in raw_values:
        parsed = _parse_ip(raw)
        if parsed is None:
            return None
        chain.append(parsed)
    return chain


def _client_ip(
    request,
    *,
    trusted_hops: int = 0,
    trusted_networks: Iterable[IPNetwork] = (),
    trust_proxy_headers: bool | None = None,
) -> str:
    """Resolve the rate-limit identity without trusting arbitrary headers.

    ``trusted_hops`` counts proxy addresses from the right-hand side of the
    request path, including the direct socket peer. Proxy headers are used only
    when every trusted hop belongs to an explicitly configured trusted network.
    Invalid or incomplete chains fail closed to the direct socket address.

    ``trust_proxy_headers`` is retained for backward compatibility with older
    tests and callers. Setting it to ``False`` disables proxy handling; setting
    it to ``True`` enables one trusted hop when ``trusted_hops`` is omitted.
    """

    direct = _parse_ip(request.client.host if request.client else "")
    direct_ip = str(direct) if direct is not None else "unknown"

    if trust_proxy_headers is False:
        return direct_ip
    if trust_proxy_headers is True and trusted_hops == 0:
        trusted_hops = 1

    networks = tuple(trusted_networks)
    if trusted_hops <= 0 or direct is None or not networks:
        return direct_ip
    if not _ip_in_networks(direct, networks):
        return direct_ip

    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        forwarded_chain = _parse_forwarded_chain(forwarded)
        if forwarded_chain is None or len(forwarded_chain) < trusted_hops:
            return direct_ip

        full_chain = [*forwarded_chain, direct]
        trusted_proxy_chain = full_chain[-trusted_hops:]
        if not all(_ip_in_networks(proxy_ip, networks) for proxy_ip in trusted_proxy_chain):
            return direct_ip

        return str(full_chain[-trusted_hops - 1])

    if trusted_hops != 1:
        return direct_ip

    real_ip = _parse_ip(request.headers.get("x-real-ip", ""))
    return str(real_ip) if real_ip is not None else direct_ip


def _route_bucket(path: str) -> str:
    normalized = "/" + "/".join(segment for segment in path.split("/") if segment)
    if normalized == "/":
        return normalized

    segments = normalized.split("/")
    return "/".join(":id" if _ID_SEGMENT.fullmatch(segment) else segment for segment in segments)


class InMemoryRateLimitMiddleware(BaseHTTPMiddleware):
    """Per-process sliding-window limiter with bounded memory.

    Proxy-provided client addresses are accepted only through the configured
    number of trusted proxy hops and trusted CIDR networks. Development and any
    untrusted direct peer use the socket address, preventing header spoofing.
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

        trusted_hops = (
            settings.proxy_trusted_hops
            if settings.app_env.strip().lower() == "production"
            else 0
        )
        client_ip = _client_ip(
            request,
            trusted_hops=trusted_hops,
            trusted_networks=settings.proxy_trusted_networks,
        )
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
