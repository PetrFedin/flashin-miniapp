# FLASHIN Production Readiness Master Plan

**Repository:** `PetrFedin/flashin-miniapp`  
**Hardening baseline:** `pilot/e2e-hardening-20260808`  
**Current verified pilot head:** `2c3a18c618e19cec4420867ed16650a985652535`  
**Purpose:** authoritative, evidence-based launch-readiness register.  
**Rule:** a capability is not `DONE` because code exists. `DONE` requires the evidence stated in this document. Unknown or externally unverifiable work is never promoted to `DONE`.

## Status vocabulary

- `DONE` — implementation and the stated repository/CI evidence are present.
- `PARTIAL` — a useful implementation exists, but one or more required production-readiness layers are not yet proven.
- `MISSING` — required implementation/evidence is not present or has not yet been established.
- `BLOCKED_EXTERNAL` — completion requires repository administration, infrastructure, credentials, DNS/provider action, or another action outside the code repository.
- `IN_PROGRESS` — an active hardening change/audit is under review and has not yet passed all required gates.
- `DEFERRED_POST_LAUNCH` — deliberately outside the launch-critical scope.

## Launch decision

**Current decision: NOT READY / NO-GO.**

The hardening branch has dedicated concurrency proof for the known refund/return, fulfillment, checkout-loyalty and cross-order referral lock inversions, an evidence-based database lock-order registry, hardened CRM recompute authority, and a corrected MoySklad outbound database/provider transaction boundary. Material CI, Security, signed backup/restore, signed rollback and production Compose isolation automation is present. Launch remains forbidden until the remaining P0/P1 risks are audited and closed, issue #119 acceptance evidence is complete, production prerequisites are configured, protected-branch controls are enabled, and controlled external-live verification is complete.

## Evidence discipline

Every launch-critical item must be traceable to at least one of:

1. merged PR/commit;
2. deterministic unit/integration test;
3. PostgreSQL/Redis/provider-specific smoke where applicable;
4. CI/Security workflow evidence;
5. release/restore/rollback evidence;
6. production/staging operational evidence where the requirement cannot be proven in repository CI.

If the evidence is only planned, the item remains `MISSING`, `PARTIAL`, `IN_PROGRESS`, or `BLOCKED_EXTERNAL`.

## Current critical register

| Priority | Area | Status | Evidence / current fact | Exit gate |
|---|---|---|---|---|
| P0 | Caddy `golang.org/x/crypto` CVE-2026-56854 | DONE | PR #201 merged after CI/Security; ingress contract requires patched dependency | Keep Trivy/security contract mandatory |
| P0 | Caddy gRPC CVE-2026-84304 | DONE | PR #202 merged; CI, Security, Trivy, SBOM, signed backup/restore, signed rollback and production Compose isolation passed | Keep built-binary version assertion and image scan mandatory |
| P0 | Refund/return DB lock ordering | DONE | PR #200 merged; canonical `Order -> ReturnRequest`, relationship revalidation and real PostgreSQL lock-order proof passed full gates | Preserve invariant in repository-wide lock audit |
| P1 | Fulfillment DB lock ordering | DONE | PR #205 exact head `710e3c9ff00bc3e872eb35eb2f34c2fd268ec0c7` passed full CI/Security/release-safety including PostgreSQL NOWAIT proof, then merged; issue #204 closed | Preserve canonical `Order -> FulfillmentTask`; audit child-row interactions separately |
| P0 | Checkout/cart loyalty DB lock ordering | DONE | PR #207 exact head `e2a3192b0fef6fc85534c589aaf2c2de438c891e` passed CI `33782393441` and Security `33782393457`, including real PostgreSQL loyalty lock-order smoke | Preserve `CrmProfile -> LoyaltyRedemptionHold`; issue #206 is closed after registry merge |
| P1 | Database lock-order evidence registry | DONE | PR #208 exact head `221cd5090ab51fa90b1fdbd3e932be8c5bfeeb6b` passed CI #1390 / `33859924724` and Security #252 / `33859924503`; merged as `fee79ffbf4c8dbe3e0fa7c004b223842b664bf90` | Keep `docs/DATABASE_LOCK_ORDER.md` updated as audit evidence evolves; registry existence does not certify the whole graph |
| P0 | Cross-order referral refund/settlement lock inversion | DONE | Issue #209 fixed by PR #210. Exact head `444beea6ca395f0d8cc37b4176f744bcfaa32478` passed CI #1393 and Security #256 including real PostgreSQL proof; merged as `aa2da3cf0d73ebb4ecee38754ffa6ba9f755c73f` | Preserve `ReferralAttribution -> ReferralCode -> CrmProfile(referrer)` ordering for shared referral identity |
| P0 | CRM recompute authorization / loyalty balance ownership | DONE | Issue #211 fixed by PR #212. Exact head `771eceb1a6ebf4353d1fb7ccc1949e993d9bb595` passed CI #1396 and Security #260; merged as `e938bda53bbd2666b91f0a291712dda7d54ee488` | Keep global CRM mutation behind `crm.recompute`; loyalty balance remains ledger/service-owned |
| P1 | MoySklad outbound DB/provider transaction boundary | DONE | Issue #213 fixed by PR #214. Exact head `4c89331769d6fdbbcdcf62287e6526e662d1413f` passed CI #1400 / `33980013694` and Security #265 / `33980013702`; merged as `2c3a18c618e19cec4420867ed16650a985652535` | Preserve snapshot -> end DB transaction -> provider HTTP -> fresh provider-command finalize boundary |
| P1 | Systematic DB lock-order inventory | IN_PROGRESS | `docs/DATABASE_LOCK_ORDER.md` is merged and records proven, one-way, potential-cycle and unverified edges; repository-wide second pass is not complete | Audit every `FOR UPDATE` and relevant mutation path; every newly proven inversion gets a separate issue/PR/PostgreSQL smoke |
| P1 | External-I/O transaction-boundary audit | PARTIAL | Payment creation, refund approval and MoySklad inbound paths were audited as transaction-safe; MoySklad outbound was fixed by #214. Telegram/notification, delivery, search/storage/email/webhook dispatch and remaining provider paths are not yet fully audited | Complete provider/network audit and eliminate long external I/O under DB transactions |
| P0 | Main branch protection | BLOCKED_EXTERNAL | Fresh repository evidence still requires protected-main verification; launch policy treats `protected:false` as NO-GO | Admin enables PR requirement, required CI/Security, no force push/deletion; verify before launch |
| P1 | Pilot/release branch protection | BLOCKED_EXTERNAL | Fresh pilot check at merge `2c3a18c6...` reports `protected:false`; code policy is enforced by process/CI, not GitHub branch control | Add appropriate protected-branch/ruleset controls before this branch is used as a release authority |
| P1 | GitHub Dependency Graph | BLOCKED_EXTERNAL | Issue #196 remains open; Security #265 used the mandatory fallback path and Trivy vulnerability scanning remained green | Enable Dependency Graph and prove differential dependency-review blocks a deliberately vulnerable dependency; retain Trivy defense in depth |
| P0 | Launch gate #119 | IN_PROGRESS | Issue #119 remains open and is the authoritative real-launch gate | Close only after all live/provider/infrastructure/governance evidence is attached and independently verifiable |
| P0 | Production domain/DNS/TLS/Telegram allowed domain | BLOCKED_EXTERNAL | Requires real deployment/domain ownership and provider configuration | Configure and verify public HTTPS, renewal, secure headers and Telegram allowed domain |
| P0 | Production Telegram credentials | BLOCKED_EXTERNAL | Real secret/provider configuration must not be fabricated in repository | Configure through approved secret backend and perform controlled live verification |
| P0 | Production YooKassa credentials/callback | BLOCKED_EXTERNAL | Real financial-provider configuration cannot be proven with CI-only evidence | Configure secret backend/callback and perform explicitly approved controlled live smoke/reconciliation |
| P0 | Production secret backend/rotation | BLOCKED_EXTERNAL | Requires deployment/admin secret-store configuration | Configure, document ownership/rotation and verify fail-closed production validation |
| P0 | Controlled external-live smoke | BLOCKED_EXTERNAL | Real provider/domain action requires explicit approval; must not be simulated as proof | Execute only after infrastructure/provider prerequisites and record evidence in #119 |
| P1 | CI backup/restore + release rollback automation | DONE | Exact-head CI #1400 again passed signed PostgreSQL backup/restore, signed full release rollback and production Compose isolation | Do not equate CI drill with production DR; production restore evidence remains required |
| P1 | Production DR operational proof | PARTIAL | DR documentation and CI drills exist; production RPO/RTO, off-host retention and production-like rehearsal are not yet fully evidenced | Define/approve RPO/RTO, retention/off-host storage and run production-like restore rehearsal |

## Proven lock-order baseline

The following pairs/chains are proven/hardened and must not be inverted by later changes:

- `Customer -> Cart`
- `Order -> PaymentCreationAttempt`
- `Order -> Payment`
- `Order -> ReturnRequest`
- `Order -> FulfillmentTask`
- `CrmProfile -> LoyaltyRedemptionHold`
- `ReferralAttribution -> ReferralCode -> CrmProfile(referrer)` for referral settlement/full-refund reversal of shared referral identity
- multi-row `ProductVariant` acquisition must remain deterministic by stable sorted/ID order where multiple variants are locked

This is **not** a complete global hierarchy. `docs/DATABASE_LOCK_ORDER.md` is the evidence registry and the repository-wide audit remains an active P1 concurrency workstream.

## Phase plan and maturity

### Phase 1 — known critical blockers

| Work item | Status | Evidence / maturity |
|---|---|---|
| #201 ingress x/crypto CVE | DONE | L5 repository/CI security evidence |
| #202 ingress gRPC CVE | DONE | L5 repository/CI security evidence |
| #200 refund/return lock order | DONE | L4 concurrency + full CI/Security/release-safety evidence |
| #204/#205 fulfillment `Order -> FulfillmentTask` | DONE | L4 concurrency + full CI/Security/release-safety evidence |
| #206/#207 loyalty `CrmProfile -> LoyaltyRedemptionHold` | DONE | L4 real PostgreSQL concurrency + full CI/Security; issue #206 closed |
| #208 lock-order/readiness registry | DONE | Documentation registry merged after exact-head CI #1390 and Security #252 |
| #209/#210 referral refund cross-order deadlock | DONE | L4 real PostgreSQL concurrency + full gates |
| #211/#212 CRM recompute authority | DONE | Dedicated mutation permission, loyalty ownership separation, atomic audit/commit + full gates |
| #213/#214 MoySklad outbound transaction boundary | DONE | Provider boundary regression coverage + exact-head CI #1400 and Security #265 |

### Phase 2 — database concurrency

Status: `IN_PROGRESS`.

Completed/hardened evidence includes the root contracts above and the cross-order referral chain from PR #210. The broader graph is not yet certified.

Required work:

- inventory every `.with_for_update()` / `FOR UPDATE` and relevant write path;
- verify pairs involving Customer, CrmProfile, Cart, CartItem, PromoCode, LoyaltyRedemptionHold/ledger, Order, Payment, PaymentCreationAttempt, ReturnRequest, FulfillmentTask, FulfillmentTaskItem, OrderItem, ProductVariant, SlaEvent, Product/pricing and outbox/domain rows;
- classify child-only mutations separately from root-lock cycles rather than assuming a conflict;
- preserve deterministic multi-row ordering;
- for every proven inversion: issue -> dedicated branch -> minimal fix -> regression test -> real PostgreSQL concurrency smoke -> full gate.

Remaining audit candidate: the local sequence in `backend/services/loyalty.py::refund_redeemed_points` reaches `LoyaltyRedemptionHold -> CrmProfile` in a path that must be compared with all same-row opposite callers. It remains `POTENTIAL_CYCLE`, not a confirmed defect, until a complete call graph and real PostgreSQL wait cycle prove it.

Exit artifact: continuously maintained `docs/DATABASE_LOCK_ORDER.md` with evidence status per edge plus a completed repository-wide second pass.

### Phase 3 — transaction boundaries and provider safety

Status: `PARTIAL`.

Evidence completed so far:

- payment creation path: DB prepare/commit -> provider I/O -> fresh finalize was audited as safe;
- return approval/refund path: prepare is committed before provider refund call, then fresh `Order -> ReturnRequest` re-lock/revalidation;
- MoySklad inbound synchronization commits before network page fetches / between durable writes;
- MoySklad outbound `customerorder`, `demand` and `salesreturn` was corrected by PR #214 so all DB-derived state becomes a frozen snapshot and the read transaction ends before provider GET/POST.

Required work:

- audit Telegram/notification transports, delivery provider, email, S3/CDN, Meilisearch/search, external webhook dispatch and remaining network call sites;
- prefer `prepare -> commit -> external call -> fresh-lock finalize` across provider boundaries;
- define explicit connect/read/write/pool timeouts;
- bounded retry + backoff/jitter only for safe/idempotent operations;
- provider idempotency keys and ambiguous-result reconciliation;
- no row locks or implicit read transactions held while waiting seconds on external providers unless an audited exception is documented.

### Phase 4 — financial integrity

Status: `PARTIAL`.

Payment/refund/cancellation/promo/loyalty must prove:

- no duplicate charge/refund/order;
- immutable monetary order snapshot;
- application `Decimal` + database `NUMERIC` consistency and deterministic rounding;
- payment/order/provider amount and currency invariants;
- cumulative refunds cannot exceed successful captured payment;
- ambiguous provider state enters reconciliation/review, never silent success;
- operator-visible reason + immutable audit for manual financial resolution;
- concurrency/failure tests for remaining critical races.

CRM recompute no longer overwrites the loyalty balance after PR #212, but a separate schema/ORM audit remains required because current ORM models may still contain SQLAlchemy `Float` declarations for monetary fields while migration `0032_fixed_precision_money.py` changed physical PostgreSQL types. No schema conclusion is promoted until actual ORM-to-schema consistency is verified.

### Phase 5 — inventory, fulfillment and delivery

Status: `PARTIAL`.

Required launch-safe outcomes:

- no oversell or negative inventory;
- controlled inventory mutation service/ledger;
- reconciliation for stock/reservations;
- canonical fulfillment state machine and lock order;
- cancelled/refunded orders cannot incorrectly progress through fulfillment;
- shipping/tracking lifecycle consistent with order/fulfillment state;
- operator recovery path for conflicts.

### Phase 6 — security and privacy

Status: `PARTIAL`.

Repository security automation is material and active. PR #212 also removed an accidental global-write permission escalation in CRM maintenance. Launch still requires completion/verification of:

- remaining admin/customer auth hardening;
- authoritative RBAC permission matrix and endpoint coverage;
- PII classification/access/masking/retention controls;
- distributed rate limiting where multiple replicas matter;
- production secret management and rotation;
- branch protection/rulesets;
- Dependency Graph enablement/differential review;
- real TLS/domain/provider configuration.

### Phase 7 — reliability and durable asynchronous work

Status: `PARTIAL`.

Verify/finish durable webhook intake, outbox/background jobs, lease ownership, retry limits, dead-letter/review queues, manual replay, provider reconciliation and scheduler singleton/fencing behavior. Critical business effects must not exist only in process memory. MoySklad provider-command finalization after external I/O now uses a fresh DB transaction with lease-token revalidation after PR #214.

### Phase 8 — observability and operations

Status: `PARTIAL`.

Launch-critical paths require structured logs/correlation IDs, metrics, actionable operator errors, review queues, SLOs, alerts and runbooks. Raw log inspection alone is not an acceptable recovery interface for money/inventory failures.

### Phase 9 — release/DR/staging

Status: `PARTIAL`.

CI already exercises substantial Docker/Compose/restore/rollback safety. Exact-head CI #1400 again proved signed backup/restore, signed full release rollback and production Compose isolation. Remaining work is to prove production-like staging, immutable release artifacts/digests, production secret/infrastructure configuration, operational backup policy and recovery rehearsal.

### Phase 10 — launch gate

Status: `BLOCKED_EXTERNAL` + `IN_PROGRESS`.

Issue #119 remains authoritative. A GO decision is forbidden while any of the following is unresolved:

- critical CI or Security failure;
- open P0 data/money/security defect;
- known critical deadlock on launch path;
- unprotected main/release control required by policy;
- missing production secrets/provider credentials;
- missing domain/TLS/callback configuration;
- unverified production-like backup/restore capability;
- missing operator ownership/approval;
- controlled external-live smoke not completed.

## Maturity scale

- `L0` — absent
- `L1` — code exists
- `L2` — unit tests
- `L3` — integration proof
- `L4` — concurrency/failure proof
- `L5` — observability + operator recovery/runbook + release gate
- `L6` — controlled production verification

Launch-critical capabilities require at least `L5`; payment/refund/provider boundaries should reach `L6` through an explicitly approved controlled live smoke.

## Required authoritative artifacts

| Artifact | Status | Notes |
|---|---|---|
| `docs/PRODUCTION_READINESS_MASTER_PLAN.md` | IN_PROGRESS | Authoritative register exists from #203 and is being synchronized in this documentation-only change through pilot `2c3a18c6...` / PR #214 evidence |
| `docs/DATABASE_LOCK_ORDER.md` | DONE | Created and merged through PR #208. The artifact exists and is authoritative; the repository-wide concurrency audit recorded inside it remains `IN_PROGRESS` |
| `docs/IDEMPOTENCY_CONTRACTS.md` | MISSING | Must map checkout/payment/refund/cancellation/webhooks/notification/shipment semantics |
| `docs/RBAC_MATRIX.md` | MISSING | Must be derived from actual named permissions/endpoints, including `crm.recompute` from PR #212 |
| `docs/SLO.md` | MISSING | Define measurable production objectives and owners |
| `docs/ERROR_CATALOG.md` | MISSING | Actionable validation/conflict/provider/security/integrity/retry/review taxonomy |
| provider contract docs | PARTIAL | Audit existing provider documentation before claiming completeness; MoySklad outbound boundary evidence now includes PR #214 |
| operational runbooks | PARTIAL | DR documentation exists; complete money/provider/webhook/inventory/secret incident runbooks and evidence |
| `docs/PRODUCTION_READINESS_FINAL_REPORT.md` | MISSING | Create only at final launch-readiness pass |

## Pull-request gate

Every hardening PR must state:

1. **Problem** — concrete failure/risk.
2. **Evidence** — code path, test, trace, CVE, invariant or reproducible concurrency condition.
3. **Root Cause** — why the risk exists.
4. **Fix** — smallest safe architectural correction.
5. **Safety** — neighboring behavior intentionally preserved.
6. **Tests** — unit/integration/concurrency/E2E/release evidence as appropriate.
7. **Rollback** — how to revert without hiding the original risk.

A PR must not be merged if required CI, Security, release rollback, restore or production-isolation checks are red, cancelled, skipped unexpectedly, or still running.

## External blocker register

`BLOCKED_EXTERNAL` does not mean optional. It means code work can continue while a named external prerequisite remains launch-blocking.

- Enable and verify `main` branch protection and required gates.
- Add appropriate protection/rulesets for the hardening/release branch before it becomes release authority.
- Enable GitHub Dependency Graph; complete issue #196 acceptance while retaining Trivy.
- Provision production PostgreSQL/Redis/networking and approved secret backend.
- Configure production domain, DNS, TLS and Telegram allowed domain.
- Provision/rotate production Telegram and YooKassa credentials.
- Configure real YooKassa callback and other required provider endpoints.
- Provision operator identities/roles and named launch owner/approver.
- Complete controlled external-live smoke with explicit approval and attach evidence to #119.

## Next execution sequence

1. Merge this readiness synchronization only after fresh exact-head CI + Security gates.
2. Continue the repository-wide lock-order second pass; keep the remaining loyalty hold/profile edge as `POTENTIAL_CYCLE` until a real opposite same-row cycle is proven or dismissed.
3. Continue the transaction-boundary audit with Telegram/notification transport, delivery provider, email, S3/CDN, Meilisearch/search and webhook dispatch; every proven network-under-transaction defect gets its own issue/branch/PR/tests.
4. For every newly proven lock inversion or transaction-boundary defect, use a dedicated minimal PR with regression coverage and real PostgreSQL/provider-boundary proof where applicable.
5. Audit ORM money types against the actual migrated PostgreSQL schema before changing financial types; separate schema corrections into focused PRs.
6. Continue P0 -> P1 through financial integrity, cancellation, inventory/fulfillment, security, reliability, observability and release readiness.
7. Keep #119 open until every real launch prerequisite is evidenced.

## Change-control rule for this document

Update this file whenever a launch-critical PR is opened, materially changes scope, is blocked by a new gate, or is merged. Promote status only when the evidence exists. If new information disproves a prior readiness claim, downgrade the status immediately rather than preserving an optimistic historical label.
