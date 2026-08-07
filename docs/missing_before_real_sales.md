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
- a fail-closed first-20-order runtime with automatic STOP on critical financial integrity failures;
- signed live lifecycle and GitHub repository-governance admission bindings;
- a mandatory `integrated-e2e` gate that drives a valid test Telegram WebApp signature through the real Mini App, FastAPI, PostgreSQL and Admin fulfillment/delivery path while replacing only the external YooKassa HTTP boundary.

The exact v20 `main` release `39c9faa0309bcf5ce669ec246d10761e47108a87` passed the `push` CI with all six jobs: `backend`, `frontend`, `admin`, `browser-e2e`, `integrated-e2e`, `docker`. This proves the internal application stack, migrations, transactional smokes, browser contracts, signed backup/restore, release rollback and production Compose isolation for that commit. It does **not** prove deployed Telegram, YooKassa, MoySklad, Meilisearch, R2/CDN, DNS/HTTPS or human-operational gates.

These capabilities are necessary but do not by themselves authorize real money.

## Mandatory gates before the controlled pilot

The pilot remains **NO-GO** until all items below are completed for the exact release and production configuration:

1. Production secrets are installed outside the repository for Telegram, YooKassa, MoySklad, Meilisearch and R2/S3 when enabled.
2. Public Mini App, API and Admin DNS names resolve to the pilot host and serve valid HTTPS certificates.
3. Terms of sale, privacy policy, consent text, return/refund rules and seller details are final and publicly accessible.
4. Named business, operations, technical, legal and support owners are recorded; an on-call escalation route and external alert receiver are active.
5. GitHub `main` is protected against direct pushes and requires the complete CI workflow before merge: strict `backend`, `frontend`, `admin`, `browser-e2e`, `integrated-e2e`, `docker`, explicit force-push/deletion restrictions and administrator/ruleset bypass policy. A fresh signed repository-governance report must bind those checks to the exact release commit and to the official GitHub Actions App ID `15368`; `any source` and spoofable status contexts are forbidden.
6. The privileged repository-governance token is injected only into the single operator command that creates the report. It is absent from root `.env`, Compose/container environments, application services, logs and evidence.
7. Current and previous immutable releases are promoted and independently verifiable.
8. Signed strict provider evidence passes for the exact release/configuration.
9. Signed live readiness evidence passes against deployed public endpoints.
10. A production-like host rollback drill completes with retained signed backup, manifest, report and measured RTO/RPO.
11. Signed live lifecycle evidence proves the deployed paths below with named owners and checksum-bound files:
   - real Telegram signed authentication;
   - YooKassa redirect and payment return;
   - duplicate payment webhook idempotency;
   - real sandbox refund and reconciliation;
   - live MoySklad synchronization;
   - Telegram notification delivery;
   - live Meilisearch indexing when enabled;
   - live R2/S3/CDN delivery when durable media is enabled.
12. All P01-P20 steps in the live pilot runner are completed with no `todo` or `failed` state.
13. The signed admission manifest includes both the live lifecycle report and the repository-governance report, and the pilot runtime is armed only for an explicit Telegram allowlist and exactly 20 orders.

Raw Telegram initData, GitHub tokens and provider secrets must never be stored in pilot evidence.

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
