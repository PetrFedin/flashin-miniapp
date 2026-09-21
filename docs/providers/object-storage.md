# Object storage provider contract

## Scope

This contract covers FLASHIN media storage when `MEDIA_STORAGE=s3|r2`.
PostgreSQL is authoritative for `MediaAsset`; S3/R2 stores binary objects.
Local filesystem mode keeps the same generated-key cleanup safety rules but is
not an external provider.

## Configuration and secrets

Required provider settings are:

- `MEDIA_STORAGE=local|s3|r2`
- `MEDIA_PUBLIC_BASE_URL`
- `S3_ENDPOINT_URL`
- `S3_BUCKET`
- `S3_REGION`
- `S3_ACCESS_KEY_ID`
- `S3_SECRET_ACCESS_KEY`
- `S3_CONNECT_TIMEOUT_SECONDS`
- `S3_READ_TIMEOUT_SECONDS`
- `S3_MAX_ATTEMPTS`
- `MEDIA_IO_MAX_CONCURRENCY` (1-8; default 4)

Credentials belong in the approved production secret mechanism and must never
be returned to clients, cleanup endpoints or logs.

## Upload authority

`POST /media/upload` requires a client `Idempotency-Key`. The admin client
persists one key across ambiguous network retries.

The authoritative path is:

1. authenticate/authorize `media.write`;
2. look up an already committed `MediaAsset.upload_key`; a retry returns it
   before object storage is touched;
3. end the request DB transaction;
4. sanitize the image and call object storage with no active SQLAlchemy transaction;
5. start a fresh DB phase, re-read the active admin and re-check `media.write`;
6. re-check the upload key to close the concurrent retry race;
7. persist `MediaAsset`, derivative metadata and audit in one DB transaction;
8. materialize the response before commit;
9. commit; from this point the object is authoritative and is never eligible
   for orphan cleanup because of later response/serialization failure.

Alembic 0044 enforces partial uniqueness for non-empty
`MediaAsset.upload_key`.

## Media validation and storage keys

Uploads are decoded and re-encoded JPEG/PNG/WebP only. MIME must match decoded
format. Animated images, invalid images, excessive dimensions/pixel counts and
oversized input/output are rejected.

Storage keys are generated server-side as 32 lowercase hexadecimal characters
plus `.jpg`, `.png` or `.webp`. Cleanup never accepts a client/operator
supplied key.

## Ambiguous provider writes

For S3/R2, the storage key exists before `put_object`. A provider can accept
an object and then lose the response. External write exceptions are therefore
wrapped as `MediaStorageWriteError` carrying only the generated storage key.

The failed request remains failed. FLASHIN persists durable cleanup work for
that exact generated key before control leaves the request. If PostgreSQL is
unavailable and the cleanup command itself cannot be committed, the application
fails loudly; it does not claim durable recovery.

## Durable cleanup command

A non-authoritative uploaded object is represented by one idempotent
`ProviderCommand`:

- `provider=object_storage`
- `command_type=object_storage.media.delete`
- `aggregate_type=media_cleanup`
- `aggregate_id=SHA-256(storage_key)`
- idempotency key derived from the same SHA-256
- payload contains only the generated storage key and fixed reason
  `upload_finalize_failed`

This covers ambiguous `put_object`, DB/RBAC finalize failure and a losing
concurrent upload retry. A successful authoritative commit is excluded.

## Cleanup worker transaction boundary

The dedicated media-cleanup worker is independent from the MoySklad worker.

1. claim `provider=object_storage` commands with the generic PostgreSQL
   `FOR UPDATE SKIP LOCKED` lease mechanism;
2. commit the claim before external I/O;
3. validate provider/type/payload/hash/idempotency bindings;
4. query PostgreSQL for any committed `MediaAsset.storage_key` reference;
5. close that read transaction;
6. if referenced, move the command to `review_required` and do not delete;
7. otherwise call storage deletion with `db.in_transaction() == False`;
8. lease-fenced finalize as `sent`, or use bounded generic retry/backoff;
9. permanent provider configuration/auth failures become `review_required`.

Deleting the same exact generated key is replay-safe. Unknown/tampered commands
fail closed before provider I/O.

The scheduler runs this worker every minute under the distributed scheduler
advisory lock. `scripts/run_media_cleanup_jobs.py` provides the same locked
one-shot execution path.

## Operator recovery

An authenticated admin with `media.write` can:

- inspect `GET /media/cleanup` for command id, status, attempts, timestamps,
  error and object fingerprint;
- replay only an existing `failed` or `review_required` command through
  `POST /media/cleanup/{command_id}/retry`.

The replay endpoint has no storage-key parameter. The persisted provider,
command type, payload, aggregate digest and idempotency binding are revalidated
before status changes. Replay is audited as `media.cleanup.retry`.

Raw command payloads and storage credentials are not returned.

## Database-outage residual recovery

A PostgreSQL-backed queue cannot persist new recovery evidence while PostgreSQL
itself is unavailable. If storage may have accepted an object and recovery
persistence cannot commit, treat the request as an incident. Reconcile provider
objects against committed `MediaAsset.storage_key` values after database
recovery; do not invent DB rows and do not bulk-delete unmatched keys without
evidence.

## Async transport execution

S3/R2 uses synchronous boto3 internally, but the request path never executes
that transport on the FastAPI event-loop thread. Client construction and
`put_object` run in a dedicated bounded executor.

- executor hard cap: 8 threads per backend process;
- runtime admission cap: `MEDIA_IO_MAX_CONCURRENCY` (1-8, default 4);
- calls waiting for capacity remain coroutines and do not create provider threads;
- boto3 connect/read timeout and bounded standard retry settings remain authoritative;
- request cancellation does not free a concurrency slot while the provider
  thread is still running;
- cancellation after a generated key enters provider execution propagates as
  cancellation while carrying that key into the durable #222 cleanup boundary;
- a provider exception that finishes after request cancellation is consumed by
  the completion callback so it cannot become an unobserved task exception.

Local filesystem mode is unchanged and does not use the S3/R2 executor.
The dedicated cleanup worker remains synchronous under the blocking scheduler;
its object deletion path is therefore not a FastAPI event-loop call.

## Evidence

Mandatory evidence includes:

- `backend/tests/test_media_storage_transaction_boundary.py`
- `backend/tests/test_media_async_transport.py`
- `backend/tests/test_media_cleanup_recovery.py`
- `scripts/media_storage_transaction_boundary_smoke.py`
- `scripts/media_cleanup_recovery_smoke.py`
- `backend/tests/test_scheduler_lock_architecture.py`
- full exact-head CI, Security and release-safety gates.
