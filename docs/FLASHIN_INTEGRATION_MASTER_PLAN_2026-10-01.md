# FLASHIN — Integration Master Plan

**Document:** `docs/FLASHIN_INTEGRATION_MASTER_PLAN_2026-10-01.md`  
**Status:** PLANNED  
**Date:** 2026-10-01

## Purpose

Canonical roadmap for the next product/commercial integrations after FLASHIN production admission. This plan does not replace the existing catalog/order/payment/refund/delivery/MoySklad/search/recommendation/CMS authorities.

## Mandatory prerequisite

FLASHIN must first clear its current production admission sequence:

`exact-main -> Render env -> /health -> dedicated PostgreSQL/Valkey -> migrations -> /ready -> deployed E2E -> signed evidence -> production gate`

Do not spend a product wave on new stateful features while dedicated durable state is unresolved.

## Integration disposition

| Capability | Source | Decision |
|---|---|---|
| Fit Profile | native | ADOPT |
| Reservation TTL | native + durable jobs | ADOPT |
| Exchange/Reverse Logistics | native | ADOPT |
| Outfit/Complete the Look | current recommendation graph | ADOPT |
| Back-in-stock/Drop waitlist | native | ADOPT |
| UX funnel/session evidence | PostHog | SIDECAR/SDK |
| Server-state UX | TanStack Query | ADOPT |
| Admin media upload | Uppy | ADOPT |
| Frontend API mocks | MSW | ADOPT/DEV |
| Frontend observability | Sentry JS | ADOPT |
| Product experiments | GrowthBook | ADAPT |
| Session replay alternative | OpenReplay | DEFER/ALTERNATIVE |

## Phase 0 — Production admission

All new migrations and jobs wait until the existing production gate is green with dedicated stateful services. Do not reuse another project's database/Redis.

## Phase 1 — Frontend reliability foundation

### TanStack Query

Introduce a single server-state layer for:

- catalog;
- product detail;
- cart;
- order;
- profile;
- reservation/waitlist;
- admin lists.

Rules:

- authoritative mutation result always comes from backend;
- optimistic update only where rollback is defined;
- stock/payment state is never trusted from local cache.

### MSW

Create deterministic frontend scenarios:

- stock race;
- payment fail/pending;
- refund pending;
- delivery unavailable;
- provider timeout;
- reservation expired.

### Sentry JS

Connect frontend errors/performance to release SHA and backend trace/correlation ID.

## Phase 2 — Fit Profile

Native entity:

- user/profile ID;
- body/fit preferences;
- usual sizes by category/brand;
- preferred fit;
- user-confirmed notes;
- provenance/source;
- updated timestamp.

Do not infer sensitive body/health attributes.

Use fit profile only as product/size assistance. Recommendation remains explainable and user-editable.

## Phase 3 — Reservation TTL

Flow:

`variant available -> reserve -> expires_at -> checkout/payment -> committed OR expiry/release`

Requirements:

- reservation token/id;
- server-side expiry;
- idempotent release;
- inventory reconciliation;
- no double allocation;
- payment timeout handling.

Do not use client timers as authority.

## Phase 4 — Exchange & Reverse Logistics

Add lifecycle:

`exchange/return request -> eligibility -> pickup/dropoff -> received -> QC -> restock/quarantine -> refund/exchange completion`

Link into existing refund/damaged/quarantine authority.

Do not create a parallel "returns database".

## Phase 5 — Waitlist / Drop subscription

Allow a user to subscribe to:

- exact variant;
- product;
- drop/collection.

On stock/event:

`inventory fact -> eligible subscribers -> frequency/consent check -> notification job -> attribution`

Waitlist does not reserve inventory unless user enters Reservation TTL flow.

## Phase 6 — Outfit / Complete the Look

Build on the existing recommendation engine.

Relationship types:

- curated-compatible;
- bought-together;
- same-look;
- complementary category.

Editorial relationship must be distinguishable from algorithmic recommendation.

Outfit cannot bypass availability/variant checks.

## Phase 7 — Product analytics

### PostHog

Instrument:

`view -> select variant -> add cart -> begin checkout -> payment -> order -> return/refund`

Also capture search-to-product and recommendation exposure.

Business truth remains backend/order/payment tables. PostHog is behavioral telemetry.

Session replay must mask personal/payment fields.

## Phase 8 — Admin media workflow

Uppy for multi-file upload:

`select -> validate -> upload progress/retry -> server admission -> media processing -> publish`

Admin cannot publish a product pointing to an unfinished/quarantined upload.

## Phase 9 — Controlled experimentation

GrowthBook only after stable event instrumentation.

Candidate experiments:

- product card layout;
- recommendation placement;
- fit-profile onboarding;
- waitlist CTA.

Do not experiment on payment security, refund eligibility, stock authority or legal terms.

OpenReplay is an alternative to PostHog session replay, not a second mandatory telemetry system. Choose one replay solution.

## Cross-cutting acceptance

- dedicated DB/Valkey first;
- inventory/payment idempotency;
- mobile Telegram Mini App E2E;
- frontend/backend trace correlation;
- provider failure behavior;
- privacy masking;
- exact-head deployment evidence.

## Prohibited

Do not:

- keep reservations only in frontend;
- infer medical/body conditions;
- let analytics own order state;
- use two session-replay products simultaneously without reason;
- build new CMS/search/recommendation engines that duplicate existing ones;
- merge return and refund state incorrectly.

## Suggested issue order

1. FLASH-INT-00 Complete production admission.
2. FLASH-INT-01 TanStack Query + MSW.
3. FLASH-INT-02 Frontend Sentry.
4. FLASH-INT-03 Fit Profile.
5. FLASH-INT-04 Reservation TTL.
6. FLASH-INT-05 Reverse Logistics.
7. FLASH-INT-06 Waitlist/Drop alerts.
8. FLASH-INT-07 Outfit graph.
9. FLASH-INT-08 PostHog instrumentation.
10. FLASH-INT-09 Uppy admin upload.
11. FLASH-INT-10 Controlled experiments.

**Implementation instruction:** after infrastructure admission, prioritize commerce conversion and post-purchase quality rather than further generic platform hardening.

## Additional wave — admin passkeys, release signing and cross-stack tracing

### Admin passkeys / step-up authentication — ADOPT

Server reference: https://github.com/duo-labs/py_webauthn

Add WebAuthn/passkeys to the high-risk admin contour first.

Roles/actions:

- administrator;
- catalogue/content publisher;
- refund/return operator;
- inventory/provider configuration;
- remote-config/feature-flag changes;
- export of customer/order data.

A passkey is attached to the existing FLASHIN admin identity.

Require recent/step-up authentication for high-impact actions such as:

- refund/manual payment intervention;
- provider secret/configuration change;
- mass catalogue publish;
- export of personal/order data;
- role/permission changes.

Recovery and credential removal require audit evidence.

### Cross-stack OpenTelemetry — ADOPT

References:

- https://github.com/open-telemetry/opentelemetry-python
- https://github.com/open-telemetry/opentelemetry-js
- https://github.com/open-telemetry/opentelemetry-collector-contrib

Use OpenTelemetry for distributed trace context across:

`Mini App/Admin -> API -> PostgreSQL/Redis -> background job -> YooKassa/MoySklad/search/storage provider -> webhook/reconciliation`

Keep current Sentry for errors and Prometheus/Grafana for metrics; OpenTelemetry closes the missing trace-correlation layer rather than replacing them.

Record release SHA, correlation ID, order/job/provider reference and status class. Do not trace card/payment secrets, Telegram auth payloads, addresses or message bodies.

### Sigstore Cosign production artefact signing — ADOPT/CI

Reference: https://github.com/sigstore/cosign

After current exact-head production admission is stable, sign production container/image artefacts and attest the exact source SHA.

Recommended release chain:

`exact source SHA -> tests -> dependency/security scans -> image -> SBOM/provenance -> Cosign signature -> deploy exact digest -> post-deploy proof`

Render/runtime evidence should record the deployed image/build digest where the deployment model exposes it.

### Acceptance extension

- admin account takeover of a long-lived session cannot silently perform critical operations without step-up where configured;
- trace correlation joins frontend/admin/API/provider/reconciliation failures;
- current Sentry/Prometheus dashboards continue to work;
- production artefact provenance is cryptographically verifiable;
- none of these additions weaken the dedicated DB/Valkey prerequisite.

**Sequencing:** dedicated production state first; OpenTelemetry can be introduced during production hardening; passkeys after stable admin authentication; Cosign after build artefacts/SBOM are deterministic.

## Additional wave — customer service, promotion authority, store credit and verified UGC

This wave starts only after the dedicated production-state gate and builds on the existing order/refund/delivery/catalog authorities.

### Customer Service Case Desk — ADOPT/ADAPT

Optional support sidecar reference: https://github.com/chatwoot/chatwoot

Create a native FLASHIN support case linked to authoritative commerce entities:

- customer/user;
- order;
- delivery;
- return/refund;
- payment/provider incident;
- issue category;
- priority/status;
- assigned operator;
- message/provider references;
- SLA timestamps;
- resolution code.

If Chatwoot is adopted, use it as an agent-inbox/channel sidecar:

Telegram/web/support message -> Chatwoot conversation -> FLASHIN support-case link -> domain action through FLASHIN API

Chatwoot must not directly refund, change stock, cancel orders or mutate customer/order truth.

### Promotion / Discount Authority — ADOPT

Introduce versioned promotion rules rather than scattered coupon conditionals.

Promotion types may include:

- code-based discount;
- automatic basket promotion;
- product/category/collection campaign;
- fixed/percent discount;
- threshold;
- bundle;
- first-order;
- limited audience;
- limited redemptions.

Each promotion has:

- ID/version;
- eligibility;
- effective window;
- priority/stacking policy;
- funding/owner;
- redemption limits;
- exclusions;
- reason/audit.

Pricing must show:

base/current price -> eligible promotions -> selected/stacked rule -> final payable amount

Server recomputes the final price at checkout; client display is never price authority.

### Store Credit / Gift Balance Ledger — ADOPT

Create a liability-style ledger, not a mutable balance field.

Entries:

- issuance;
- promotional credit;
- refund-to-credit;
- redemption;
- reversal;
- expiry where legally/business permitted;
- manual adjustment with authorised reason.

Every balance equals the sum of immutable ledger entries.

Store credit redemption participates in the existing payment/order transaction and cannot make order totals negative.

If gift certificates are later exposed, issuance/redemption still uses this ledger and unique secure tokens.

### Verified Purchase Reviews / UGC — ADOPT

Create moderated review records:

- product;
- user/order line;
- verified-purchase flag;
- rating;
- text/media;
- fit/size feedback where appropriate;
- moderation status/reason;
- published version;
- abuse/report state.

Only an actual completed order line can receive verified_purchase=true.

Reviews must never become stock/product-master authority, and moderation must distinguish removal for policy violation from negative sentiment.

UGC media uses the existing secure media-admission pipeline.

### Support-to-product feedback projection — ADOPT

Derive aggregated, privacy-safe issue signals from support/returns/reviews:

- sizing issue frequency;
- defect/damage reason;
- delivery complaint;
- description mismatch;
- repeat support driver.

These are analytical projections with minimum-count/privacy thresholds. They may inform merchandising/QC but never silently rewrite product data.

### Additional acceptance

- support agents can execute sensitive actions only through existing authorised domain commands;
- promotion price is deterministic and reproducible from rule/version;
- store-credit balance reconciles exactly to immutable ledger entries;
- verified-purchase status is backed by authoritative order history;
- moderation/analytics do not suppress negative but valid customer feedback;
- analytics cannot expose individual support content.

**Sequencing:** production admission -> support-case links can start early; promotion authority before expanding coupons/campaigns; store-credit only after payment/refund reconciliation is stable; reviews/UGC after secure media moderation exists.

**Dependency hygiene:** review Chatwoot's current license/deployment/security requirements before any runtime adoption; keep the sidecar replaceable.

## Additional wave — loyalty, referrals and search merchandising

This wave reuses proven portfolio mechanics where possible instead of introducing another large platform.

### Loyalty Ledger / Tier Authority — ADOPT

Primary portfolio reference:

- `PetrFedin/MFW/mfw-api/brand365-store.js`
- existing MFW Brand365 offer / continuous-membership / reward / redemption patterns.

Reuse the **patterns**, not the database or event model.

FLASHIN native entities should cover:

- loyalty account;
- points/benefit ledger entry;
- tier;
- qualification rule/version;
- reward/benefit;
- redemption;
- expiry/reversal;
- campaign/source attribution.

Balance/tier must be reproducible from ledger/rules, not an unexplained mutable field.

Possible earn sources:

- completed order;
- approved campaign;
- verified referral;
- explicit service recovery;
- manually authorised adjustment.

Returns/refunds must reverse the relevant earned value deterministically.

### Referral Authority — ADOPT

Create explicit referral lifecycle:

referral invite/link -> referred user identified -> eligibility -> qualifying order -> anti-abuse checks -> reward pending -> return-window/confirmation -> reward issued

Store:

- referrer;
- referred account;
- referral code/token;
- campaign/version;
- attribution timestamp;
- qualification event;
- fraud/duplicate status;
- reward ledger links.

Do not reward merely for a click/registration if the campaign requires a real completed order.

Self-referrals, recycled accounts and duplicate devices/payment identities should enter an explicit review/deny rule rather than ad-hoc manual logic.

### Search Merchandising Layer — ADOPT

FLASHIN already has Meilisearch. Add a governed merchandising projection rather than a second search system.

Support versioned:

- pinned products for a query/category;
- explicit bury/exclusion;
- campaign/category boosts;
- synonym sets;
- typo/normalisation dictionaries;
- availability-aware filtering;
- new-drop/freshness boosts;
- sponsored/promoted placement with explicit disclosure where applicable.

Search configuration is editorial/commercial policy; product/stock/price facts still come from canonical FLASHIN state.

### Search Quality Feedback — ADOPT

Track:

query -> results shown -> product open -> add-to-cart -> order

And quality signals:

- zero-result queries;
- reformulation;
- high-exit query;
- searched product unavailable;
- search result rank vs downstream conversion.

Use these to review synonyms/merchandising, not to auto-rewrite product metadata.

### External loyalty-platform note

Historical/open-source loyalty engines exist, but current licensing/member-limit/commercial constraints vary. For FLASHIN, the safer near-term path is a native bounded ledger using the already proven MFW reward/redemption concepts. Re-evaluate a third-party loyalty sidecar only if rule complexity or multi-channel partner programs materially outgrow this model.

### Additional acceptance

- loyalty balance/tier can be recomputed from authoritative entries;
- refund/reversal cannot leave earned value orphaned;
- referral reward is idempotent and linked to a qualifying order;
- merchandising cannot surface unpublished/unavailable items as purchasable;
- search configuration has version/owner/effective dates;
- loyalty/referral events remain subordinate to order/payment/refund truth.

**Sequencing:** payment/refund production proof -> loyalty ledger -> referral qualification -> search merchandising/quality loop.

## Additional wave — barcode receiving, cycle count and inventory confidence

This wave strengthens physical-stock accuracy without turning FLASHIN into a full warehouse management system.

### Browser barcode/QR scanner — ADOPT

Reference:

https://github.com/zxing-js/browser

Use scanning in staff/admin surfaces for:

- receiving supplier/MoySklad-linked inventory;
- variant lookup;
- return intake;
- quarantine placement;
- stocktake/cycle count;
- reservation/pick verification.

Scanner output is an identifier candidate only. Server resolves it to the canonical FLASHIN variant/barcode mapping.

### Barcode Mapping Authority — ADOPT

Create native mappings:

- variant ID;
- code type;
- barcode/value;
- source/provider;
- status;
- effective dates;
- created/reviewed by.

Support multiple historical/provider codes if business reality requires it.

Do not replace internal variant IDs with an external barcode string.

### Receiving Verification — ADOPT

Flow:

expected receipt/provider document -> scan item/carton -> resolved variant -> observed quantity -> discrepancy -> accepted/quarantine/review -> authoritative inventory event

Track:

- expected vs observed;
- operator;
- timestamp;
- location;
- damage/quality flag;
- provider/MoySklad reference;
- discrepancy reason.

### Cycle Count — ADOPT

Create bounded stocktake sessions:

- location;
- expected snapshot/version;
- counted quantity;
- scan events;
- discrepancy;
- reviewer;
- reconciliation decision.

A count never directly overwrites stock. It creates an approved inventory adjustment through the existing stock/inventory authority.

### Inventory Confidence Projection — ADOPT

For each variant/location, derive an operational confidence signal from facts such as:

- age since last verified count/receipt;
- unresolved discrepancy;
- recent return/quarantine;
- provider reconciliation status.

Use categories such as:

- verified recently;
- normal;
- review recommended;
- unresolved discrepancy.

Do not display this as exact probability unless a validated statistical model actually exists.

### Additional acceptance

- unknown barcode cannot mutate inventory;
- duplicate scans are handled deterministically;
- receiving discrepancy links to source/provider document;
- cycle-count correction requires authorised reconciliation;
- stock/order/reservation authority remains unchanged;
- scanner failure degrades to manual variant lookup.

**Sequencing:** dedicated production state + inventory/provider reconciliation first -> barcode mapping -> receiving scan -> cycle count -> inventory-confidence projection.

**Dependency note:** ZXing browser library is currently MIT-licensed upstream; pin the version and test Telegram/iPhone camera behavior before production use.

