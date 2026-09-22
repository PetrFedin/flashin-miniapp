# Idempotency contracts

This document defines the identities that FLASHIN preserves across retries and
the cases where retry is intentionally blocked because an external side effect
may already have happened.

## External webhook delivery

Every durable `webhook_outbox` row has one stable event identity:

- `X-Flashin-Event-Id: <webhook_outbox.id>`
- `X-Flashin-Event-Type: <event_type>`
- the HMAC signature is recomputed over the same stored payload for every send.

Manual replay never creates a new outbox row and therefore never changes the
event ID.

### Receiver idempotency is not assumed

FLASHIN does **not** assume an arbitrary external webhook receiver deduplicates
`X-Flashin-Event-Id`. A receiver should store that ID and make processing
idempotent, but production safety does not depend on that behavior unless a
separate receiver contract proves it.

Therefore delivery outcomes are classified as follows:

| Classification | Meaning | Automatic action |
| --- | --- | --- |
| `safe_retry_pre_dispatch` | failure is proven before receiver dispatch (for example TCP/TLS connection establishment or pool acquisition) | bounded retry |
| `ambiguous_transport` | request may have reached receiver but local transport did not prove the response | `review_required` |
| `ambiguous_cancelled` | worker/request cancellation happened after dispatch began | `review_required` |
| `ambiguous_http` | receiver returned a non-success status where side-effect acceptance is not safe to infer | `review_required` |
| `permanent_contract` | payload, signing, URL/security or other deterministic pre-dispatch contract is invalid | `review_required` |
| `permanent_http` | deterministic 4xx response (except 408/425/429) | `review_required` |

A non-2xx response is never blindly retried.

### Review recovery

A `review_required` row is excluded from worker claims. An operator with
`webhooks.write` must reconcile the stable event ID with the receiver and use
one explicit action:

- `POST /api/outbox/{id}/review/mark-sent` after receiver evidence confirms
  the event was processed; this performs no network send.
- `POST /api/outbox/{id}/review/retry` only after evidence confirms replay is
  safe or receiver support explicitly authorizes replay.

Both actions require the request body to repeat the exact `event_id` and one
bounded reason code. Generic retry endpoints cannot bypass review quarantine.

Allowed reason codes:

- `receiver_confirmed_not_processed`
- `receiver_confirmed_processed`
- `receiver_support_authorized_replay`
- `configuration_corrected`

The audit log stores only event ID, bounded classification, reason code and
attempt count; it does not store raw receiver errors or secret webhook paths.

## Media upload

Media upload idempotency and ambiguous provider-write cleanup are documented in
`docs/providers/object-storage.md`.

## Provider commands

MoySklad and object-storage command identities are defined by their durable
provider-command contracts. A command retry reuses its persisted idempotency
key; it never invents a new external identity for the same business action.
