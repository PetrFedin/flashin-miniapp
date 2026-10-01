# Distributed rate-limit authority

## Purpose

FLASHIN production rate limiting is a shared security authority, not a per-process cache.

Every production backend replica must use the same shared dataset. Sensitive routes are allowed only when that authority can make an atomic decision. Local in-memory limiting remains available only for development and isolated tests.

## Production contract

Required production values:

- `RATE_LIMIT_ENABLED=true`
- `RATE_LIMIT_BACKEND=redis` **or** `RATE_LIMIT_BACKEND=postgres`

Redis/Valkey mode additionally requires `RATE_LIMIT_REDIS_URL=redis://...`, `rediss://...`, `valkey://...`, or `valkeys://...`.

PostgreSQL mode uses the authoritative `DATABASE_URL` and migration `0047_postgres_rate_limit_authority`; no Redis URL is required.

The bundled production Compose deployment keeps Redis as its default shared authority. External production-admission contours may use PostgreSQL when they have a dedicated durable Postgres database but no isolated Redis/Valkey resource.

## Route budgets

Dedicated budgets exist for:

- Telegram authentication;
- Admin login/password-reset confirmation;
- product search;
- checkout;
- payment creation;
- return/refund requests;
- support ticket creation;
- YooKassa webhook ingress;
- general API traffic.

Exact numeric budgets are environment configuration and can be changed without changing route-policy semantics.

## Identity scopes

Every limited request consumes an IP-scoped budget derived from the trusted single-Caddy client-IP contract.

Sensitive authenticated customer operations also consume a bearer-session fingerprint budget. Raw bearer tokens are never stored in either shared backend.

Checkout additionally consumes an `Idempotency-Key` fingerprint budget when the header is present. Raw idempotency keys are never stored.

### Redis/Valkey authority

The decision across all applicable keys is one atomic Lua operation. If one scope is exhausted, no other scope is partially consumed.

### PostgreSQL authority

Hits are stored in `rate_limit_hits`. The limiter acquires deterministic transaction-scoped PostgreSQL advisory locks for every applicable key, evaluates the 60-second sliding window using database time, and inserts all accepted hits in the same transaction. If one scope is exhausted, the transaction inserts none of the keys.

This preserves shared multi-instance and fail-closed semantics without requiring a second datastore.

## Failure policy

Fail closed when the shared limiter is unavailable:

- authentication;
- Admin authentication;
- checkout;
- payment creation;
- returns/refunds;
- payment/refund webhooks.

These requests receive HTTP 503 with `Retry-After: 1`.

Fail open, but emit degraded telemetry:

- general API reads;
- search;
- support ticket creation.

Allowed degraded responses include `X-RateLimit-Degraded: open`.

Normal budget exhaustion returns HTTP 429 with `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining` and `X-RateLimit-Policy`.

## Persistence and restart behavior

Redis/Valkey mode stores state in sorted sets over the sliding-window horizon. The bundled Redis uses AOF persistence, and CI proves an active budget survives a Redis container restart.

PostgreSQL mode stores the same short-lived security state durably in `rate_limit_hits`. Restarting an application instance cannot reset the budget because the database, not process memory, is authoritative. Expired rows are pruned transactionally.

## Monitoring

Prometheus exposes:

- `flashin_rate_limit_backend_available`;
- `flashin_rate_limit_decisions_total{category,outcome}`.

Alerts cover shared-backend failure, fail-closed decisions and sustained rate-limit rejections.

## Operator response

If `FlashinRateLimitBackendUnavailable` fires:

1. keep sensitive routes closed;
2. identify the configured shared backend;
3. for Redis/Valkey, verify service health, URL, persistence and storage;
4. for PostgreSQL, verify `DATABASE_URL`, database reachability, current Alembic head and the `rate_limit_hits` table;
5. do not switch production to `memory` as a workaround;
6. restore the configured shared authority and confirm `flashin_rate_limit_backend_available == 1`;
7. review recent `backend_fail_closed`, `backend_fail_open` and `rejected` counters.

If the shared authority cannot be restored safely, commercial checkout remains closed.

## Proof

The repository contains:

- unit tests for route policy, identity hashing and production configuration;
- `scripts/rate_limit_redis_smoke.py` for Redis shared-budget and concurrency proof;
- `scripts/rate_limit_persistence_smoke.sh` for Redis restart persistence;
- `scripts/rate_limit_postgres_smoke.py` for PostgreSQL multi-key atomicity and concurrent single-winner proof;
- migration `0047_postgres_rate_limit_authority`;
- `.github/workflows/distributed-rate-limit-state.yml` for exact-head proof of both shared backends;
- production Compose validation for Redis isolation, health, AOF and durable storage.
