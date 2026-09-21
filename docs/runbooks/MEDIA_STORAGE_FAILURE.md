# Runbook: media object-storage failure

## Trigger

Use this runbook when a media upload fails during S3/R2 write, fresh DB
finalization, durable cleanup persistence, or cleanup execution.

## Safety invariants

- PostgreSQL `MediaAsset` is authoritative.
- A provider object without committed `MediaAsset.storage_key` evidence is an
  orphan candidate, not a valid catalog asset.
- A committed `MediaAsset` object must never be deleted by orphan cleanup.
- Cleanup may target only a FLASHIN-generated 32-hex JPEG/PNG/WebP key.
- Never repair an incident by manually creating a successful `MediaAsset`
  without verified provider and DB evidence.

## Normal recovery model

When object storage may contain an object but upload authority did not commit,
FLASHIN persists:

- provider `object_storage`
- command `object_storage.media.delete`
- an immutable SHA-256 fingerprint binding to the generated key.

The worker claims and commits its lease before storage I/O, checks that no
committed `MediaAsset` references the key, closes the DB read transaction and
only then deletes. Success/failure is finalized with the same lease token.

## Ambiguous S3/R2 timeout

A timeout does not prove rejection by the provider.

1. Capture request/correlation evidence.
2. Confirm the request failed; do not tell the operator/client the upload succeeded.
3. Check `GET /media/cleanup` for the cleanup command/status.
4. If pending/processing, allow the worker to reconcile.
5. If `failed` or `review_required`, verify the provider incident and use
   command-id-only replay if appropriate.
6. Do not submit or edit a storage key through an operator API; no such API is supported.

## DB/RBAC finalize failure after successful provider write

The upload request rolls back the non-authoritative DB phase and commits a
cleanup command for the generated key.

Expected state:

- no committed `MediaAsset` for that storage key;
- one idempotent cleanup command;
- failed request remains failed;
- cleanup worker eventually reaches `sent`, or exposes terminal evidence.

If durable command persistence itself fails, escalate: the application cannot
claim DB-backed recovery while PostgreSQL is unavailable.

## Failure after authoritative commit

Once the `MediaAsset` transaction commits, later response/serialization or
client disconnect failure does **not** make the object an orphan.

Recovery:

1. retry with the same media upload `Idempotency-Key`, or
2. use authorized `GET /media/uploads/{upload_key}`.

The existing committed `MediaAsset` is returned without another provider
write. No cleanup command should be created for the committed object.

## Cleanup command states

- `pending`: eligible for worker claim after `next_attempt_at`.
- `processing`: leased to one worker.
- `sent`: delete completed/idempotently reconciled.
- `failed`: bounded retries exhausted; operator review required.
- `review_required`: malformed/tampered command, permanent provider error, or
  a committed `MediaAsset` reference blocks deletion.

Only `failed` and `review_required` commands can be manually requeued.
Replay preserves the stored key binding and is audited.

## PostgreSQL outage / disaster recovery

If object storage may have accepted a write while PostgreSQL cannot persist the
cleanup command:

1. preserve incident timestamps/request ids and provider evidence;
2. restore PostgreSQL authority first;
3. enumerate candidate provider objects using approved storage tooling;
4. compare candidates with committed `MediaAsset.storage_key` values;
5. protect every referenced object;
6. create/execute recovery only for evidence-backed orphans.

Do not bulk-delete unmatched provider objects based only on age/name.

## Verification after recovery

Verify:

- every surviving provider object used by catalog media has a committed DB reference;
- cleanup commands have an explainable terminal/current state;
- no cleanup command deleted an object referenced by `MediaAsset`;
- retry with a known upload key returns the committed asset without another provider write;
- storage calls did not run inside an active SQLAlchemy transaction;
- S3/R2 upload calls executed off the FastAPI event-loop thread;
- cancellation did not release provider concurrency before the in-flight call finished;
- no credentials/raw provider payloads appear in operator responses or logs.

## Escalation

- repeated provider outage blocking media operations: operational incident;
- PostgreSQL outage plus ambiguous provider writes: data-integrity recovery incident;
- evidence of cleanup deleting referenced media: immediate data-integrity escalation;
- credential compromise/unauthorized object access: security incident process.

## S3/R2 request execution

Upload transport is offloaded from the FastAPI event loop through the bounded
media I/O executor. If a request is cancelled while boto3 is still running,
FLASHIN preserves the generated storage key, persists the normal durable cleanup
command when no authoritative `MediaAsset` committed, and propagates
cancellation. Do not interpret client cancellation as proof that S3/R2 rejected
the write.

If uploads queue behind the media I/O concurrency limit, investigate provider
latency/timeouts before raising `MEDIA_IO_MAX_CONCURRENCY`. The value is
deliberately capped at 8 so operator tuning cannot create unbounded provider
thread fan-out.
