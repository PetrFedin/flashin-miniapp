# Pilot order incident trace

Use the order incident trace when one controlled pilot order is delayed, inconsistent, or needs operator investigation.

## Endpoint

`GET /api/ops/orders/{order_id}/trace`

The endpoint is admin-only and requires the existing `orders.read` permission. Responses are explicitly `no-store` and include the HTTP `request_id` so an operator can correlate the trace lookup with application/Sentry logs.

The durable lifecycle correlation key is the FLASHIN `order_id`. A request id is useful for one HTTP request, but it is not a durable business identifier across asynchronous YooKassa callbacks, workers, MoySklad commands, fulfillment, refunds, or Telegram delivery.

## What the trace shows

The response aggregates sanitized state for the same order across:

- checkout attempt identity (without checkout idempotency key or request fingerprint);
- payment records and YooKassa payment-event metadata;
- returns/refunds;
- durable external-provider commands, including MoySklad effects;
- fulfillment tasks;
- business-event processing/recovery state;
- deterministic order-linked notification delivery;
- SLA events;
- a compact `attention` summary.

## Privacy and secret boundary

The trace is intentionally an operational metadata view. It does **not** read or return raw webhook/provider payloads, provider command payloads, idempotency keys, request fingerprints, confirmation URLs, Telegram ids, notification bodies, or free-form provider/notification error text.

Do not expand this endpoint with raw provider payloads merely for convenience. Use provider-side consoles and existing restricted evidence/recovery procedures when raw external data is genuinely required.

## Incident flow

1. Identify the affected FLASHIN `order_id`.
2. Open the trace and record its returned `request_id` in the incident notes.
3. Inspect `attention.required` and the per-section statuses.
4. If a provider command is pending/processing/failed/review-required, use the corresponding provider/reconciliation runbook; do not create a duplicate side effect manually.
5. If a business event is unresolved, use the existing business-event recovery controls rather than editing the database.
6. If a deterministic notification is failed, use the existing notification requeue endpoint/worker flow.
7. If money state is inconsistent, stop the pilot money path and use the payment/refund reconciliation controls before accepting another affected order.
8. Re-open the trace after recovery and verify the order-linked sections have converged.

## Admission boundary

This trace is for fast operations and incident diagnosis. It is **not** a substitute for signed lifecycle evidence, repository-governance evidence, the P01-P20 checklist, or the final pilot admission gate.
