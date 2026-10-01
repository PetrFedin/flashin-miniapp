#!/usr/bin/env python3
"""Transactional proof for the PostgreSQL distributed rate-limit authority."""

from __future__ import annotations

import asyncio
import json
import uuid

from sqlalchemy import text

from backend.database import SessionLocal
from backend.services.distributed_rate_limit import PostgresDistributedRateLimiter


async def run() -> dict:
    prefix = f"flashin:rate-limit:smoke:{uuid.uuid4().hex}"
    key_a = f"{prefix}:a"
    key_b = f"{prefix}:b"
    key_c = f"{prefix}:c"
    key_race = f"{prefix}:race"
    limiter = PostgresDistributedRateLimiter()

    try:
        assert await limiter.ping()

        first = await limiter.hit((key_a, key_b), 2)
        second = await limiter.hit((key_a, key_c), 2)
        denied = await limiter.hit((key_a, key_b), 2)

        assert first.allowed is True and first.remaining == 1
        assert second.allowed is True and second.remaining == 0
        assert denied.allowed is False
        assert denied.retry_after_seconds >= 1

        with SessionLocal() as db:
            counts = dict(
                db.execute(
                    text(
                        "SELECT bucket_key, count(*)::int "
                        "FROM rate_limit_hits "
                        "WHERE bucket_key IN (:a, :b, :c) "
                        "GROUP BY bucket_key"
                    ),
                    {"a": key_a, "b": key_b, "c": key_c},
                ).all()
            )
        assert counts == {key_a: 2, key_b: 1, key_c: 1}

        race = await asyncio.gather(
            *[limiter.hit((key_race,), 1) for _ in range(4)]
        )
        assert sum(1 for decision in race if decision.allowed) == 1
        assert sum(1 for decision in race if not decision.allowed) == 3

        return {
            "ok": True,
            "multi_key_atomic": True,
            "concurrent_single_winner": True,
        }
    finally:
        with SessionLocal() as db:
            with db.begin():
                db.execute(
                    text("DELETE FROM rate_limit_hits WHERE bucket_key LIKE :prefix"),
                    {"prefix": prefix + "%"},
                )
        await limiter.close()


def main() -> int:
    report = asyncio.run(run())
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
