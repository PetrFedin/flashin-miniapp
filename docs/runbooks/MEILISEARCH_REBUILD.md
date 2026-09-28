# Runbook: Meilisearch rebuild / provider failure

## Trigger

Use this runbook when catalog search is stale, Meilisearch is unavailable, an admin rebuild/configuration call fails, or an index mutation has an ambiguous timeout.

## Safety model

PostgreSQL is the source of truth. Meilisearch is derived state. Do not edit Product, price, inventory, payment, order, or refund data in order to make Meilisearch look correct.

Admin rebuild/configure endpoints end their SQLAlchemy transaction before provider network I/O. A provider timeout therefore must not leave request-owned DB locks/transactions open.

## Initial checks

1. Confirm the API itself is healthy and PostgreSQL is available.
2. Confirm `MEILISEARCH_ENABLED=true` in the intended environment.
3. Confirm Meilisearch URL/key are supplied by the approved secret/configuration mechanism.
4. Check provider reachability and authentication without printing the API key.
5. Review application/provider logs using request/correlation identifiers; do not log credentials.

## Rebuild

Invoke the permission-protected `POST /search/admin/rebuild` operation as an authorized catalog operator. The operation:

1. rebuilds the local PostgreSQL-backed search representation;
2. snapshots active Product/ProductImage fields into primitive documents;
3. releases the read transaction;
4. submits the detached documents to the configured Meilisearch product index.

A successful HTTP response confirms that the provider accepted the document task request; it does not replace later provider-health verification where operational evidence is required.

## Configure index settings

Invoke the permission-protected `POST /search/admin/configure-meili` operation when index searchable/filterable/sortable/ranking settings need to be restored. The operation is deterministic and can be re-run after investigating a previous ambiguous provider response.

## Timeout or connection failure

`MEILISEARCH_TIMEOUT_SECONDS` bounds each SDK request. When a mutation times out:

- do not assume the provider did not receive it;
- do not add an automatic blind retry loop;
- inspect Meilisearch task/provider state if available;
- re-run the deterministic rebuild/configure operation after provider health is restored;
- verify representative active and inactive product behavior.

## Verification

After recovery verify:

- an active product is discoverable;
- inactive products are not returned through the active filter;
- price/id sorting is accepted by the index;
- local fallback search still works if Meilisearch is disabled/no IDs are returned;
- API DB pool/transaction metrics do not show a lingering transaction caused by the provider call.

## Escalation

Escalate as SEV2 if catalog discovery is materially degraded while checkout/order/payment integrity remains unaffected. Escalate according to the incident policy if the failure is part of a broader infrastructure/security outage.

## Rollback

The application change associated with Issue #219 has no schema migration. Code rollback is a normal release rollback. Do not roll back authoritative catalog data merely because the derived index failed.
