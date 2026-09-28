# Runbook: webhook delivery review

## Trigger

Use this runbook when a webhook outbox row has status `review_required` or
the alert `FlashinWebhookOutboxReviewRequired` fires.

## Safety invariant

Do not replay merely because FLASHIN did not receive a successful response.
A timeout, read error, cancellation or selected HTTP response can occur after
the receiver has already performed the side effect.

`X-Flashin-Event-Id` is the durable reconciliation identity. FLASHIN preserves
the same ID across manual replay.

## Classifications

- `ambiguous_transport`: dispatch started; local transport cannot prove the
  receiver outcome.
- `ambiguous_cancelled`: execution was cancelled after dispatch began.
- `ambiguous_http`: non-2xx response does not prove whether the receiver
  performed the business side effect.
- `permanent_contract`: deterministic local contract/configuration problem.
- `permanent_http`: deterministic 4xx receiver contract problem.
- `safe_retry_pre_dispatch`: no receiver dispatch was established; worker
  retry is automatic and does not enter review unless attempts exhaust.

Raw provider exceptions and secret destination paths are intentionally absent
from the operator response.

## Reconciliation procedure

1. Read the outbox row and record its `id`, event type, classification and
   attempt count.
2. Use receiver logs/support to search for the exact
   `X-Flashin-Event-Id`.
3. If the receiver confirms processing, use
   `POST /api/outbox/{id}/review/mark-sent` with the same `event_id` and
   reason code `receiver_confirmed_processed`.
4. If the receiver confirms it did not process the event, use the explicit
   review replay endpoint with `receiver_confirmed_not_processed`.
5. If receiver support authorizes an idempotent replay despite uncertainty,
   use `receiver_support_authorized_replay`.
6. After correcting a deterministic configuration problem, replay may use
   `configuration_corrected`.
7. Confirm the row leaves `review_required` and the Prometheus review gauge
   returns to zero.

## Forbidden recovery

- Do not use the generic `/{id}/retry` endpoint for `review_required`.
- Do not create a replacement outbox row to obtain a new event ID.
- Do not edit the stored payload or destination directly in PostgreSQL.
- Do not mark a row sent without receiver evidence.
- Do not expose raw destination paths, query tokens, signing secrets or raw
  provider errors in tickets/screenshots.

## Metrics and audit

Prometheus exports:

- `flashin_webhook_outbox_metrics_collection_success`
- `flashin_webhook_outbox_review_required_total`
- `flashin_webhook_outbox_review_required{classification=...}`

Review quarantine and both operator resolution actions create structured audit
records. Stale lease holders cannot quarantine, retry or finalize a row owned
by another worker.
