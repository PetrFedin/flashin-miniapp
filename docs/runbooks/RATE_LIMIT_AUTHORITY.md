# Distributed rate-limit authority

## Purpose

FLASHIN production rate limiting is a shared security authority, not a per-process cache.

Every production backend replica uses the same Redis dataset. Sensitive routes are allowed only when the shared limiter can make an atomic decision. Local in-memory limiting remains available only for development and isolated tests.

## Production contract

Required production values:

- `RATE_LIMIT_ENABLED=true`
- `RATE_LIMIT_BACKEND=redis`
- `RATE_LIMIT_REDIS_URL=redis://...` or `rediss://...`

The bundled production Compose deployment provides an internal Redis service with no published host port, a healthcheck, a named `/data` volume, AOF enabled and `appendfsync everysec`.

The backend waits for healthy Redis before production start.

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

Exact numeric budgets are environment configuration and can be changed without changing the route-policy semantics.

## Identity scopes

Every limited request consumes an IP-scoped budget derived from the trusted single-Caddy client-IP contract.

Sensitive authenticated customer operations also consume a bearer-session fingerprint budget. Raw bearer tokens are never stored in Redis.

Checkout additionally consumes an `Idempotency-Key` fingerprint budget when the header is present. Raw idempotency keys are not stored.

The Redis decision across all applicable keys is one atomic Lua operation. If one scope is exhausted, no other scope is partially consumed.

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

Rate-limit state is stored in Redis sorted sets and expires after the sliding-window horizon.

The bundled Redis uses AOF persistence. CI proves that an active budget survives a Redis container restart. A restart must therefore not silently reset a security-sensitive budget.

## Monitoring

Prometheus exposes:

- `flashin_rate_limit_backend_available`;
- `flashin_rate_limit_decisions_total{category,outcome}`.

Alerts cover shared-backend failure, fail-closed decisions and sustained rate-limit rejections.

## Operator response

If `FlashinRateLimitBackendUnavailable` fires:

1. keep sensitive routes closed;
2. verify Redis container/service health and storage;
3. verify `RATE_LIMIT_REDIS_URL` from the deployed environment;
4. verify AOF and `appendfsync` configuration;
5. do not switch production to `memory` as a workaround;
6. restore Redis and confirm `flashin_rate_limit_backend_available == 1`;
7. review recent `backend_fail_closed`, `backend_fail_open` and `rejected` counters.

If Redis cannot be restored safely, commercial checkout remains closed until the shared authority is restored.

## Proof

The repository contains:

- unit tests for route policy, identity hashing and atomic local fallback;
- `scripts/rate_limit_redis_smoke.py` for two-client shared-budget and concurrency proof;
- `scripts/rate_limit_persistence_smoke.sh` for restart persistence;
- `.github/workflows/distributed-rate-limit-state.yml` for exact-head Redis proof;
- production Compose validation for isolation, health, AOF and durable storage.
