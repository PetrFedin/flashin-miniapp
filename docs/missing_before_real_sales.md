# FLASHIN: remaining gates before real sales

This document is the current blocker register. It replaces the old MVP-era list.

## Code-level status

The repository already contains and tests:

- protected Telegram/customer and Admin authentication;
- catalog, cart, deterministic pricing, promotions, loyalty and referrals;
- idempotent checkout and YooKassa payment/webhook/reconciliation paths;
- returns, partial/full refunds and loyalty reversal;
- order-linked inventory reserve/commit/release ledger;
- fulfillment, shipment, tracking and delivery completion;
- support, privacy, notifications, webhooks, business events and scheduler operations;
- monitoring, signed backup/restore and full signed release rollback;
- a fail-closed first-20-order runtime with automatic STOP on critical financial integrity failures.

These capabilities are necessary but do not by themselves authorize real money.

## Mandatory gates before the controlled pilot

The pilot remains **NO-GO** until all items below are completed for the exact release and production configuration:

1. Production secrets are installed outside the repository for Telegram, YooKassa, MoySklad, Meilisearch and R2/S3 when enabled.
2. Public Mini App, API and Admin DNS names resolve to the pilot host and serve valid HTTPS certificates.
3. Terms of sale, privacy policy, consent text, return/refund rules and seller details are final and publicly accessible.
4. Named business, operations, technical, legal and support owners are recorded; an on-call escalation route and external alert receiver are active.
5. GitHub `main` is protected against direct pushes and requires the complete CI workflow before merge. Administrator bypass is restricted and audited.
6. Current and previous immutable releases are promoted and independently verifiable.
7. Signed strict provider evidence passes for the exact release/configuration.
8. Signed live readiness evidence passes against deployed public endpoints.
9. A production-like host rollback drill completes with retained signed backup, manifest, report and measured RTO/RPO.
10. Signed live lifecycle evidence proves the deployed paths below with named owners and checksum-bound files:
   - real Telegram signed authentication;
   - YooKassa redirect and payment return;
   - duplicate payment webhook idempotency;
   - real sandbox refund and reconciliation;
   - live MoySklad synchronization;
   - Telegram notification delivery;
   - live Meilisearch indexing when enabled;
   - live R2/S3/CDN delivery when durable media is enabled.
11. The signed admission manifest includes the live lifecycle report and the pilot runtime is armed only for an explicit Telegram allowlist and exactly 20 orders.

Raw Telegram initData and provider secrets must never be stored in pilot evidence.

## Mandatory gates after the first 20 orders

Mass launch remains forbidden until:

- all 20 pilot slots are reconciled against PostgreSQL order, payment, refund and inventory records;
- no unresolved STOP, review-required, stock, payment, refund, notification or fulfillment incident remains;
- Finance confirms provider settlements/refunds and amounts;
- Operations confirms inventory, pick/pack, delivery and support outcomes;
- Legal/privacy requests and retention handling are confirmed;
- backup restoration and rollback evidence are retained outside the application host;
- the final signed pilot decision is GO and a separate mass-launch approval is issued.

A successful code CI run is not a substitute for these deployed, provider, repository-governance and human-operational gates.
