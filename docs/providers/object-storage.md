# Object storage provider contract

## Scope

This contract covers FLASHIN media storage when `MEDIA_STORAGE=s3|r2`. PostgreSQL is authoritative for `MediaAsset` metadata; S3/R2 stores binary objects. Local filesystem mode uses the same generated-key cleanup safety contract but is not an external provider.

## Configuration

- `MEDIA_STORAGE=local|s3|r2`
- `MEDIA_PUBLIC_BASE_URL`
- `S3_ENDPOINT_URL`
- `S3_BUCKET`
- `S3_REGION`
- `S3_ACCESS_KEY_ID`
- `S3_SECRET_ACCESS_KEY`
- `S3_CONNECT_TIMEOUT_SECONDS` (default `5`, allowed `1..60`)
- `S3_READ_TIMEOUT_SECONDS` (default `20`, allowed `1..120`)
- `S3_MAX_ATTEMPTS` (default `3`, allowed `1..10`)

Credentials belong in the approved production secret backend and must never be returned to clients or written to logs.

## Upload transport and transaction boundary

The shared boto3 client uses bounded connect/read timeouts, Botocore `standard` retry mode and bounded `total_max_attempts`.

`POST /media/upload` uses:

1. authenticate/authorize admin and snapshot primitive `admin_id`;
2. end request DB transaction;
3. sanitize image and perform `put_object` without active SQLAlchemy transaction;
4. start a fresh DB phase, re-read active admin and re-check `media.write`;
5. atomically persist `MediaAsset`, local derivative metadata and audit.

No live ORM admin object crosses the provider boundary.

## Validation

Uploads remain restricted to decoded JPEG/PNG/WebP. Declared MIME must match decoded format. Animated images, oversized files/dimensions/pixel counts and invalid images are rejected. Images are re-encoded after EXIF orientation normalization. Storage keys are generated server-side as 32 lowercase hex characters plus `.jpg`, `.png` or `.webp`.

## Durable cleanup recovery

If an object may exist but upload finalization cannot complete, cleanup is represented by a durable `ProviderCommand`:

- `provider=object_storage`
- `command_type=object_storage.media.delete`
- `aggregate_type=media_cleanup`
- payload contains only the exact server-generated storage key and fixed reason `upload_finalize_failed`;
- `aggregate_id` is SHA-256 of that key;
- idempotency key is derived from the same SHA-256.

The command is persisted in a fresh DB phase after the failed finalize. A dedicated scheduler worker claims commands with PostgreSQL `SKIP LOCKED`, commits the claim/lease, then calls storage deletion with no active DB transaction. Finalization/retry is lease-token fenced through the generic `ProviderCommand` machinery.

Retries are bounded by the existing provider-command policy. Exhausted commands become `failed`; malformed/tampered commands become `review_required` before storage I/O. Deleting the same exact generated key is replay-safe: deleting an already absent object is an acceptable successful end state for cleanup.

## Ambiguous `put_object` outcome

S3-compatible providers can accept an object while the client times out before receiving the response. `save_media` therefore raises `MediaStorageWriteError` carrying the already generated key for any external write exception. The API persists a cleanup command for that key even though no successful provider response was received. It never accepts a client/operator-provided cleanup target.

## Operator recovery

Authorized operators with `media.write` can:

- inspect `GET /media/cleanup` for safe command metadata/status/error and an object fingerprint; the raw command payload/credentials are not exposed;
- replay only an existing `failed` or `review_required` command with `POST /media/cleanup/{command_id}/retry`.

Replay has no `storage_key` parameter. The persisted provider/type/key/hash/idempotency binding is revalidated before status changes. Operator replay is audited as `media.cleanup.retry`.

## Residual database-outage limitation

A PostgreSQL-backed durable queue cannot persist recovery work while PostgreSQL itself is unavailable. If object storage accepted a write and both finalize and cleanup-command persistence fail because the database is down, the application fails loudly; it must not claim durable recovery exists. Storage-vs-DB reconciliation during/after such an incident remains an operational DR action documented in `docs/runbooks/MEDIA_STORAGE_FAILURE.md`.

## Async execution limitation

Synchronous boto3 calls in the async upload request still block the FastAPI event-loop thread. Bounded offload/concurrency is separate issue #223 and is not closed by cleanup recovery.

## Evidence

- `backend/tests/test_media_storage_transaction_boundary.py` proves transaction-clean upload I/O, fresh authorization, ambiguous-write key preservation and loud recovery-persistence failure.
- `backend/tests/test_media_cleanup_recovery.py` proves generated-key allowlisting, binding verification, transaction-clean worker dispatch and fail-closed malformed commands.
- `scripts/media_storage_transaction_boundary_smoke.py` proves upload boundary on real PostgreSQL.
- `scripts/media_cleanup_recovery_smoke.py` proves durable enqueue/idempotency, claim-before-delete, fresh lease-fenced finalize, malformed-command review and replay safety on real PostgreSQL.
- Both smokes are mandatory CI steps; exact-head full CI, Security and release-safety remain required before merge.
