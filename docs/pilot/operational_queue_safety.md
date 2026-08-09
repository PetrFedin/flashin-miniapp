# FLASHIN pilot operational queue safety

## Purpose

The controlled pilot must not keep accepting new paid orders when its durable delivery spines are no longer draining safely. Runtime admission therefore evaluates operational queue health before every pilot checkout and exposes the same redacted decision through the protected pilot operations endpoint.

This guard complements, rather than replaces, signed admission, first-20 runtime limits, payment/refund circuit breakers and provider reconciliation.

## Scope

Operational safety is scoped to the current `PilotRuntimeState.opened_at` timestamp.

- A newly armed pilot does not inherit historical queue failures from a previous run.
- A stopped and resumed pilot keeps the same `opened_at`, so unresolved failures cannot be hidden by a resume.
- An active runtime without `opened_at` is an integrity failure and checkout is rejected.
- Only aggregate counts, ages and machine-readable blocker codes are exposed. Payloads, provider IDs, Telegram IDs, destinations, idempotency keys and error text are never returned by the safety snapshot.

## Immutable grace window

The pilot backlog grace window is **15 minutes** and is defined in release code, not in `.env`.

This is intentional. An operator cannot weaken the accepted backlog window after admission by changing runtime configuration. Changing this policy requires a new code release and therefore new release/admission evidence.

Fresh pending work inside the grace window is visible but does not block checkout. Work that is already terminal or has an expired processing lease blocks immediately.

## Guarded durable spines

| Spine | Blocking conditions |
|---|---|
| MoySklad provider commands | `failed`, `review_required`, expired processing lease, or due pending command older than 15 minutes |
| Business events | `failed` or pending event older than 15 minutes |
| Webhook outbox | `failed`, expired processing lease, or due pending webhook older than 15 minutes |
| Telegram notifications | `failed`, expired/missing processing lease, or due pending notification older than 15 minutes |

Unknown persisted statuses are also fail-closed where the underlying schema permits them.

## Checkout behavior

`backend.services.pilot_runtime.acquire_pilot_checkout()` evaluates the operational snapshot after signed runtime/database evidence and before allocating a pilot order slot.

If the current-run queues are unhealthy:

- no new pilot slot is consumed;
- checkout returns the existing customer-safe `423 pilot_checkout_unavailable` response;
- provider payloads or internal failure reasons are not disclosed to the customer;
- the runtime is not permanently transitioned to `stopped` solely for transient backlog pressure;
- after operators recover/replay the durable queue and the blocker disappears, the same admitted runtime may continue.

A failure to evaluate the safety snapshot itself is treated as runtime-integrity failure and remains fail-closed.

## Operator visibility

`GET /api/ops/pilot-runtime` is the operator source of truth. Its `operational_safety` section contains:

- `healthy`;
- `blocking_codes`;
- immutable `grace_minutes`;
- current-run `scope_started_at`;
- identifier-free counts and oldest actionable age for each guarded spine.

The endpoint remains protected by the existing security-read permission and `no-store` caching policy.

## Recovery sequence

When `operational_safety.healthy` is false, do not bypass the guard. Resolve the durable record using the existing recovery path for its spine:

1. inspect the protected operations/diagnostic views and logs;
2. determine whether the item is retryable, requires replay, or requires provider review;
3. use the existing worker/recovery controls rather than editing database state manually;
4. verify the item reaches its expected terminal success state;
5. re-read `/api/ops/pilot-runtime` and require `operational_safety.healthy=true` before accepting the next pilot checkout.

Payment/refund integrity or reconciliation blockers remain governed by the separate money circuit breaker and can still keep overall `checkout_decision=NO-GO` even when operational queues are healthy.

## Automated evidence

Repository CI proves this layer at three levels:

- `backend/tests/test_pilot_runtime.py` — current-run queue failure blocks checkout with a redacted response, historical pre-run failure is ignored, and missing `opened_at` fails integrity;
- `backend/tests/test_pilot_operations_observability.py` — operator `GO/NO-GO` mirrors operational safety without leaking durable-command details;
- `backend/tests/test_pilot_operational_safety.py` — fresh, historical, terminal, overdue and expired-lease semantics across MoySklad commands, business events, webhook outbox and Telegram notification delivery.

The existing mandatory worker/recovery smokes remain the evidence that each durable spine can actually drain/recover. Live provider and deployed-worker evidence is still required before real-money pilot `GO`.
