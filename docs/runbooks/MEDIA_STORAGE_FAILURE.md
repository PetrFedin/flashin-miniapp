# Runbook: media object-storage failure

## Trigger

Use this runbook when `/media/upload` fails during S3/R2 upload, database finalization, durable cleanup persistence or cleanup-worker deletion.

## Safety model

- PostgreSQL is authoritative for `MediaAsset` metadata.
- A provider object without a committed `MediaAsset` is an orphan candidate, not a valid catalog asset.
- Never manufacture a successful DB row simply because an object may exist remotely.
- Never delete an arbitrary storage key. Automated/manual cleanup may target only the exact server-generated key cryptographically bound into a durable cleanup command.

## Normal transaction boundary

1. admin authorization completes;
2. request DB transaction ends;
3. `put_object` runs without active SQLAlchemy transaction;
4. fresh DB transaction revalidates admin/RBAC and persists `MediaAsset` + audit.

Provider latency correlated with open request DB transactions is a regression.

## Ambiguous upload/provider failure

A timeout does not prove that S3/R2 rejected a write. FLASHIN preserves the generated object key in `MediaStorageWriteError` and persists `provider=object_storage`, `command_type=object_storage.media.delete` recovery work. Do not retry the whole upload merely to discover provider state.

If durable cleanup persistence succeeds, the dedicated scheduler worker owns recovery. It commits the claim/lease before delete, retries bounded failures, and finalizes with a fresh lease-fenced DB transaction.

## DB finalize failure after successful upload

The failed finalize transaction is rolled back. The request then persists one idempotent cleanup command for the generated key. The request remains failed; cleanup does not convert the upload into success.

## Operator queue

Use `GET /media/cleanup` with an account holding `media.write`.

Review:

- `pending` — waiting for next attempt;
- `processing` — leased by a worker;
- `sent` — deletion completed (the external id contains only a deletion fingerprint, not the key);
- `failed` — bounded automatic attempts exhausted;
- `review_required` — command binding/type/payload failed safety validation.

The list endpoint returns an object fingerprint and bounded error text, not credentials or raw provider-command payload.

## Manual replay

Use `POST /media/cleanup/{command_id}/retry` only after the underlying provider/configuration issue is understood.

The endpoint:

- accepts command id only, never a storage key;
- allows only `failed`/`review_required` rows;
- revalidates provider/type/key/hash/idempotency binding;
- preserves the recorded delete target;
- writes `media.cleanup.retry` audit evidence.

If binding validation fails, do not edit the DB row to force execution. Investigate possible corruption/tampering.

## PostgreSQL unavailable during recovery persistence

A DB-backed queue cannot durably record cleanup while its source of truth is unavailable. If upload may have reached storage and recovery-command persistence also fails, the request fails loudly.

After DB recovery:

1. preserve request/correlation evidence and provider incident window;
2. identify S3/R2 objects created during the window through approved storage tooling;
3. compare them with committed `MediaAsset.storage_key` values and existing object-storage cleanup commands;
4. classify unmatched generated-key objects as orphan candidates;
5. do not delete them from an ad-hoc API or SQL edit; use approved incident procedure and retain evidence.

This storage-vs-DB incident reconciliation is the residual DR path; repository code does not claim that PostgreSQL can record work while PostgreSQL is down.

## Authorization changed during upload

If the admin becomes inactive or loses `media.write` after object storage I/O, finalize fails closed with 403 and durable cleanup is queued. Never bypass the fresh authorization check.

## Verification after recovery

Verify:

- no committed `MediaAsset` points to a deleted cleanup object;
- successful media has matching `MediaAsset.storage_key` and upload audit;
- unresolved cleanup commands are visible and owned;
- provider delete executes without active SQLAlchemy transaction;
- credentials are absent from errors, logs and incident notes;
- public media URLs resolve only for intended committed assets.

## Escalation

- repeated provider outage blocking catalog operations: normally SEV2;
- credential compromise/unauthorized object access: security incident severity by exposure;
- uncontrolled deletion or evidence of data loss: data-integrity/SEV1 review.

## Related gap

- #223 — synchronous boto3 in the async upload request still needs bounded event-loop offload; separate P1.
