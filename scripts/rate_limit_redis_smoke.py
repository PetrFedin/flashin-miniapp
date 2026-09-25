#!/usr/bin/env python3
"""Prove shared Redis rate-limit authority across independent backend clients."""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

from redis.asyncio import Redis

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.distributed_rate_limit import DistributedRateLimiter


async def _main() -> None:
    url = os.environ.get("RATE_LIMIT_REDIS_URL", "redis://127.0.0.1:6379/0")
    prefix = f"flashin:rate-limit:smoke:{uuid.uuid4().hex}"
    raw = Redis.from_url(url, decode_responses=False)
    left = DistributedRateLimiter(
        url,
        connect_timeout_seconds=1,
        socket_timeout_seconds=1,
    )
    right = DistributedRateLimiter(
        url,
        connect_timeout_seconds=1,
        socket_timeout_seconds=1,
    )

    shared = f"{prefix}:shared"
    concurrent = f"{prefix}:concurrent"
    ip_key = f"{prefix}:ip"
    session_key = f"{prefix}:session"
    try:
        assert await left.ping()
        assert await right.ping()

        sequential = []
        for index in range(6):
            limiter = left if index % 2 == 0 else right
            sequential.append(await limiter.hit((shared,), 6))
        assert all(item.allowed for item in sequential)
        blocked = await right.hit((shared,), 6)
        assert blocked.allowed is False
        assert blocked.retry_after_seconds >= 1

        async def hit(index: int):
            limiter = left if index % 2 == 0 else right
            return await limiter.hit((concurrent,), 10)

        decisions = await asyncio.gather(*(hit(index) for index in range(40)))
        allowed = sum(1 for decision in decisions if decision.allowed)
        rejected = len(decisions) - allowed
        assert allowed == 10, (allowed, rejected)
        assert rejected == 30, (allowed, rejected)

        first_scope = await left.hit((ip_key,), 1)
        assert first_scope.allowed is True
        atomic_block = await right.hit((ip_key, session_key), 1)
        assert atomic_block.allowed is False
        assert int(await raw.zcard(session_key)) == 0

        print(
            {
                "status": "ok",
                "two_clients_share_budget": True,
                "concurrent_allowed": allowed,
                "concurrent_rejected": rejected,
                "multi_key_rejection_is_atomic": True,
            }
        )
    finally:
        await raw.delete(shared, concurrent, ip_key, session_key)
        await left.close()
        await right.close()
        await raw.aclose()


if __name__ == "__main__":
    asyncio.run(_main())
