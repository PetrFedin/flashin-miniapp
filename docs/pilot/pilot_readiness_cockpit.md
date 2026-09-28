# Pilot readiness cockpit

`GET /api/ops/pilot-readiness` is the read-only operator verdict for deciding whether the controlled pilot may accept the **next** order after the pilot runtime has already been admitted and armed.

It is intentionally different from `make pilot-launch-preflight` and the signed launch-admission chain. The cockpit cannot deploy, attach evidence, create or refund a payment, arm or stop the pilot runtime, or prove that a real external provider lifecycle happened.

## Access and safety

- Admin authentication is required.
- The caller must have the `security.read` permission.
- Responses are marked `Cache-Control: no-store, max-age=0` and `Pragma: no-cache`.
- The response carries the current `request_id` so an operator can correlate the lookup with API/Sentry records.
- The payload contains only status booleans, bounded counts, runtime state and stable blocking/warning codes. It does not copy provider payloads, credentials, webhook bodies, free-form provider errors, customer Telegram IDs or message bodies.

The Admin `PilotOperationsPanel` reads both `/api/ops/pilot-readiness` and `/api/ops/pilot-runtime` on the same refresh cycle. Its visible decision is `GO` only when both independently normalize to `GO`. If a refresh fails, the previous runtime/readiness snapshots are cleared rather than retained; a stale historical `GO` therefore cannot remain the operator's current decision after connectivity or authorization is lost.

## GO contract

`decision=GO` and `ready_for_next_order=true` are returned only when all of the following are true at the time of the request:

1. Critical service diagnostics are healthy: database connectivity, current Alembic migration state, production environment prerequisites, YooKassa configuration, MoySklad configuration, scheduler, notification delivery, webhook outbox and MoySklad synchronization.
2. The pilot runtime itself returns `checkout_decision=GO`.
3. Runtime database integrity is healthy.
4. Signed runtime/release artifact integrity is applicable and healthy.
5. There is no payment/refund/reconciliation money-attention condition for pilot orders.
6. Operational queue safety is applicable and healthy.

A missing evaluation is fail-closed. For example, missing runtime artifact evidence, unavailable operational safety or migration drift produces `NO-GO`; it is never interpreted as healthy.

## Advisory signals

Media storage and search are reported separately as advisory signals. A degraded search/media surface is visible in `warning_codes`, but it does not by itself override the money/order-delivery control plane. Operators should still investigate it before broadening the pilot.

## Operator workflow

Check the cockpit before the first controlled order after runtime arm, before each subsequent pilot order, and immediately after any payment, webhook, fulfillment, MoySklad, refund or notification incident.

If the result is `NO-GO`:

1. Do not accept the next pilot order.
2. Inspect `/api/ops/pilot-runtime` for runtime integrity, money-attention and operational queue details.
3. Inspect `/api/diagnostics` for the failing service area, including database migration state.
4. For an affected order, use `/api/ops/orders/{order_id}/trace` to correlate durable order/payment/provider-command/fulfillment/notification/SLA state.
5. If money integrity, artifact integrity or operational safety is compromised, stop the pilot runtime using the documented operator stop procedure rather than bypassing the cockpit.
6. Resume only after the underlying condition is reconciled and the cockpit returns `GO` again.

## Scope boundary

A cockpit `GO` means only: **the currently armed controlled runtime is healthy enough to accept the next allowlisted pilot order according to the in-system safety signals available now**.

It does not mean the repository is promotable, the release is admitted, branch protection is correct, provider evidence is signed, or the full pilot is complete. Those remain governed by the immutable release, repository-governance, real-provider lifecycle, P01-P20 checklist and final launch-admission gates.
