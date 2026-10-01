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
