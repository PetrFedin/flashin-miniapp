from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from typing import Iterable

from opentelemetry import trace
from redis.asyncio import Redis
from redis.exceptions import RedisError


_WINDOW_MS = 60_000
_KEY_TTL_MS = 61_000
_TRACER = trace.get_tracer("flashin.rate_limit")

_ATOMIC_SLIDING_WINDOW = r"""
local window_ms = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local member = ARGV[3]
local now = redis.call('TIME')
local now_ms = (tonumber(now[1]) * 1000) + math.floor(tonumber(now[2]) / 1000)
local min_remaining = limit
local max_retry_ms = 0

for _, key in ipairs(KEYS) do
  redis.call('ZREMRANGEBYSCORE', key, 0, now_ms - window_ms)
  local count = tonumber(redis.call('ZCARD', key))
  local remaining = limit - count
  if remaining < min_remaining then
    min_remaining = remaining
  end
  if count >= limit then
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    if oldest[2] then
      local retry_ms = window_ms - (now_ms - tonumber(oldest[2]))
      if retry_ms > max_retry_ms then
        max_retry_ms = retry_ms
      end
    else
      if window_ms > max_retry_ms then
        max_retry_ms = window_ms
      end
    end
  end
end

if max_retry_ms > 0 then
  return {0, math.max(min_remaining, 0), math.max(max_retry_ms, 1)}
end

for _, key in ipairs(KEYS) do
  redis.call('ZADD', key, now_ms, member)
  redis.call('PEXPIRE', key, 61000)
end
return {1, math.max(min_remaining - 1, 0), 0}
"""


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: int = 0


class RateLimitBackendUnavailable(RuntimeError):
    pass


def normalize_redis_url(redis_url: str) -> str:
    """Accept Redis and Valkey URI schemes while using redis-py's TLS semantics."""
    value = str(redis_url or "").strip()
    lowered = value.lower()
    if lowered.startswith("valkeys://"):
        return "rediss://" + value[len("valkeys://"):]
    if lowered.startswith("valkey://"):
        return "redis://" + value[len("valkey://"):]
    return value


class DistributedRateLimiter:
    """Atomic multi-key sliding-window authority backed by one Redis dataset."""

    def __init__(
        self,
        redis_url: str,
        *,
        connect_timeout_seconds: float,
        socket_timeout_seconds: float,
        client: Redis | None = None,
    ):
        self._client = client or Redis.from_url(
            normalize_redis_url(redis_url),
            decode_responses=False,
            socket_connect_timeout=connect_timeout_seconds,
            socket_timeout=socket_timeout_seconds,
            health_check_interval=30,
        )

    async def hit(self, keys: Iterable[str], limit: int) -> RateLimitDecision:
        unique_keys = tuple(dict.fromkeys(str(key) for key in keys if str(key)))
        if not unique_keys:
            raise ValueError("At least one rate-limit key is required")
        if limit <= 0:
            raise ValueError("Rate-limit budget must be positive")

        member = uuid.uuid4().hex
        with _TRACER.start_as_current_span("flashin.rate_limit.hit") as span:
            span.set_attribute("flashin.rate_limit.key_count", len(unique_keys))
            span.set_attribute("flashin.rate_limit.limit", int(limit))
            try:
                raw = await self._client.eval(
                    _ATOMIC_SLIDING_WINDOW,
                    len(unique_keys),
                    *unique_keys,
                    _WINDOW_MS,
                    int(limit),
                    member,
                )
            except (RedisError, OSError, TimeoutError) as exc:
                span.set_attribute("flashin.rate_limit.backend_status", "unavailable")
                raise RateLimitBackendUnavailable("shared rate-limit backend unavailable") from exc

        if not isinstance(raw, (list, tuple)) or len(raw) != 3:
            raise RateLimitBackendUnavailable("shared rate-limit backend returned invalid decision")

        allowed = bool(int(raw[0]))
        remaining = max(int(raw[1]), 0)
        retry_ms = max(int(raw[2]), 0)
        span = trace.get_current_span()
        if span.is_recording():
            span.set_attribute("flashin.rate_limit.allowed", allowed)
            span.set_attribute("flashin.rate_limit.remaining", remaining)
        return RateLimitDecision(
            allowed=allowed,
            remaining=remaining,
            retry_after_seconds=max(math.ceil(retry_ms / 1000), 1) if retry_ms else 0,
        )

    async def ping(self) -> bool:
        try:
            return bool(await self._client.ping())
        except (RedisError, OSError, TimeoutError):
            return False

    async def close(self) -> None:
        await self._client.aclose()
