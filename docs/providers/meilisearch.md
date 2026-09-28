# Meilisearch provider contract

## Purpose

Meilisearch is a derived search index for FLASHIN catalog discovery. PostgreSQL remains the authoritative source of truth for Product/ProductImage state. Search-index loss or staleness must never mutate authoritative commerce data.

## Configuration

- `MEILISEARCH_ENABLED`
- `MEILISEARCH_URL`
- `MEILISEARCH_MASTER_KEY`
- `MEILISEARCH_PRODUCTS_INDEX`
- `MEILISEARCH_TIMEOUT_SECONDS` (default 5; application validation permits 1-60 seconds)

Production configuration rejects the placeholder/missing master key when Meilisearch is enabled.

## Authentication

The official `meilisearch` Python client uses the configured master/API key. Secrets must be supplied through the production secret backend and must never be committed or logged.

## Request timeout

Every client instance is created with the bounded SDK `timeout` from `MEILISEARCH_TIMEOUT_SECONDS`. No Meilisearch request is allowed to inherit an infinite/default-unbounded application policy.

## Database transaction boundary

Admin mutation paths use the following contract:

1. authorize using PostgreSQL;
2. complete/commit any durable local search-index writes;
3. load all Product/ProductImage data required by the provider request;
4. materialize detached primitive dictionaries;
5. end the remaining read/request transaction;
6. perform Meilisearch network I/O without an open SQLAlchemy transaction.

Provider transport functions must not receive live ORM objects from request paths. This prevents lazy relationship loads and DB connection/lock retention during provider latency.

Public `/search/products` performs its Meilisearch query before its first PostgreSQL query. If Meilisearch is disabled or returns no IDs, the existing local PostgreSQL-backed search fallback remains authoritative for that request.

## Product index schema

Searchable attributes:

- `title`
- `sku`
- `brand`
- `category`
- `description`

Filterable attributes:

- `brand`
- `category`
- `gender`
- `active`
- `price`

Sortable attributes:

- `price`
- `id`

Ranking rules:

1. `words`
2. `typo`
3. `proximity`
4. `attribute`
5. `sort`
6. `exactness`

The hardening change for Issue #219 must not change these semantics.

## Retry and ambiguous outcomes

The application does not automatically blind-retry Meilisearch mutation calls. `add_documents` and settings updates are accepted by Meilisearch as asynchronous tasks, so a transport timeout can occur after the provider accepted the request. Blind retry is unnecessary for correctness and can obscure the actual provider outcome.

The supported operator recovery is to inspect provider health/task state and re-run the deterministic rebuild/configuration operation. These operations are intended to converge the derived index to the current PostgreSQL snapshot.

## Webhooks

No Meilisearch webhook is used by FLASHIN for catalog indexing.

## Reconciliation / rebuild

`POST /search/admin/rebuild` rebuilds the local search representation and publishes active-product documents to Meilisearch. PostgreSQL remains the source of truth. A future zero-downtime alias/swap reindex remains a separate readiness item; this contract does not claim it is implemented.

## Failure taxonomy

- connection/timeout/provider unavailable: provider failure; local DB remains authoritative;
- authentication/authorization failure: configuration/security failure;
- invalid index settings/document: non-retryable until configuration/data is corrected;
- ambiguous mutation timeout: inspect/re-run; do not automatically declare success;
- stale/missing derived index: rebuild from PostgreSQL.

## Operational evidence

Issue #219 adds regression tests proving transaction-clean provider calls, detached documents, bounded timeout, unchanged fallback behavior, and provider-failure cleanup. Full CI and Security on the exact PR head remain mandatory before merge.
