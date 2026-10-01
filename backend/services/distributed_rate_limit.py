from __future__ import annotations

import asyncio
import math
import uuid
from dataclasses import dataclass
from typing import Iterable

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from backend.database import SessionLocal


_WINDOW_MS = 60_000
_KEY_TTL_MS = 61_000

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
            raise RateLimitBackendUnavailable("shared rate-limit backend unavailable") from exc

        if not isinstance(raw, (list, tuple)) or len(raw) != 3:
            raise RateLimitBackendUnavailable("shared rate-limit backend returned invalid decision")

        allowed = bool(int(raw[0]))
        remaining = max(int(raw[1]), 0)
        retry_ms = max(int(raw[2]), 0)
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



class PostgresDistributedRateLimiter:
    """Atomic multi-key sliding-window authority backed by PostgreSQL."""

    def __init__(self, *, session_factory=SessionLocal):
        self._session_factory = session_factory

    async def hit(self, keys: Iterable[str], limit: int) -> RateLimitDecision:
        unique_keys = tuple(dict.fromkeys(str(key) for key in keys if str(key)))
        if not unique_keys:
            raise ValueError("At least one rate-limit key is required")
        if limit <= 0:
            raise ValueError("Rate-limit budget must be positive")

        try:
            return await asyncio.to_thread(self._hit_sync, unique_keys, int(limit))
        except (SQLAlchemyError, OSError, TimeoutError) as exc:
            raise RateLimitBackendUnavailable("shared rate-limit backend unavailable") from exc

    def _hit_sync(self, keys: tuple[str, ...], limit: int) -> RateLimitDecision:
        remaining = limit
        retry_after_seconds = 0

        with self._session_factory() as db:
            with db.begin():
                for key in sorted(keys):
                    db.execute(
                        text(
                            "SELECT pg_advisory_xact_lock("
                            "hashtextextended(CAST(:bucket_key AS text), 0)"
                            ")"
                        ),
                        {"bucket_key": key},
                    )

                db.execute(
                    text(
                        "DELETE FROM rate_limit_hits "
                        "WHERE occurred_at <= clock_timestamp() - interval '61 seconds'"
                    )
                )

                for key in keys:
                    row = (
                        db.execute(
                            text(
                                "SELECT "
                                "count(*)::int AS hit_count, "
                                "min(occurred_at) AS oldest, "
                                "GREATEST("
                                "EXTRACT(EPOCH FROM ("
                                "min(occurred_at) + interval '60 seconds' - clock_timestamp()"
                                ")), 0"
                                ") AS retry_seconds "
                                "FROM rate_limit_hits "
                                "WHERE bucket_key = :bucket_key "
                                "AND occurred_at > clock_timestamp() - interval '60 seconds'"
                            ),
                            {"bucket_key": key},
                        )
                        .mappings()
                        .one()
                    )
                    hit_count = int(row["hit_count"] or 0)
                    remaining = min(remaining, max(limit - hit_count, 0))
                    if hit_count >= limit:
                        retry_after_seconds = max(
                            retry_after_seconds,
                            max(math.ceil(float(row["retry_seconds"] or 60.0)), 1),
                        )

                if retry_after_seconds:
                    return RateLimitDecision(
                        allowed=False,
                        remaining=0,
                        retry_after_seconds=retry_after_seconds,
                    )

                for key in keys:
                    db.execute(
                        text(
                            "INSERT INTO rate_limit_hits (bucket_key, occurred_at) "
                            "VALUES (:bucket_key, clock_timestamp())"
                        ),
                        {"bucket_key": key},
                    )

        return RateLimitDecision(
            allowed=True,
            remaining=max(remaining - 1, 0),
            retry_after_seconds=0,
        )

    async def ping(self) -> bool:
        try:
            return bool(await asyncio.to_thread(self._ping_sync))
        except (SQLAlchemyError, OSError, TimeoutError):
            return False

    def _ping_sync(self) -> bool:
        with self._session_factory() as db:
            return int(db.execute(text("SELECT 1")).scalar_one()) == 1

    async def close(self) -> None:
        return None
