# FLASHIN Production Readiness Master Plan

**Repository:** `PetrFedin/flashin-miniapp`  
**Hardening baseline:** `pilot/e2e-hardening-20260808`  
**Purpose:** authoritative, evidence-based launch-readiness register.  
**Rule:** a capability is not `DONE` because code exists. `DONE` requires the evidence stated in this document. Unknown or externally unverifiable work is never promoted to `DONE`.

## Status vocabulary

- `DONE` — implementation and the stated repository/CI evidence are present.
- `PARTIAL` — a useful implementation exists, but one or more required production-readiness layers are not yet proven.
- `MISSING` — required implementation/evidence is not present or has not yet been established.
- `BLOCKED_EXTERNAL` — completion requires repository administration, infrastructure, credentials, DNS/provider action, or another action outside the code repository.
- `IN_PROGRESS` — an active hardening change is under review and has not yet passed all required merge gates.
- `DEFERRED_POST_LAUNCH` — deliberately outside the launch-critical scope.

## Launch decision

**Current decision: NOT READY / NO-GO.**

This is intentional. The pilot branch has substantial hardening and release-safety automation, but launch remains blocked until all P0 items and launch-critical P1 items are proven, issue #119 acceptance evidence is complete, production prerequisites are configured, protected-branch controls are enabled, and controlled external-live verification is complete.

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
| P0 | Caddy `golang.org/x/crypto` CVE-2026-56854 | DONE | PR #201 merged after CI and Security; ingress contract pins patched dependency | Keep Trivy/security contract mandatory |
| P0 | Caddy gRPC CVE-2026-84304 | DONE | PR #202 merged; CI, Security, Trivy, SBOM, signed backup/restore, signed rollback and production Compose isolation passed | Keep built-binary version assertion and image scan mandatory |
| P0 | Refund/return DB lock ordering | IN_PROGRESS | PR #200 enforces `Order -> ReturnRequest`; dedicated PostgreSQL lock-order smoke exists; synchronized with current ingress security baseline | Merge only after fresh CI + Security + Docker/release-safety are fully `completed/success` |
| P1 | Fulfillment DB lock ordering | MISSING | Current audit proves generic fulfillment path can lock `FulfillmentTask -> Order`, while settlement/creation path uses `Order -> FulfillmentTask` | Canonicalize `Order -> FulfillmentTask`; add relation revalidation, unit regression and real PostgreSQL NOWAIT smoke; full CI/Security |
| P1 | Systematic DB lock-order inventory | PARTIAL | Several root pairs are already proven by prior hardening, but no complete authoritative repository-wide lock matrix exists yet | Create/maintain `docs/DATABASE_LOCK_ORDER.md`; audit every `FOR UPDATE`/mutation path; each proven inversion gets a separate PR |
| P1 | External-I/O transaction-boundary audit | PARTIAL | Refund hardening demonstrates prepare/external/finalize discipline, but repository-wide provider/network audit is not complete | Audit payment, refund, delivery, MoySklad, Telegram, search/storage/notifications; eliminate long external I/O under DB row locks |
| P0 | Main branch protection | BLOCKED_EXTERNAL | Known repository state has `main` unprotected; current connector cannot administer repository protection/rulesets | Admin enables PR requirement, required CI/Security, no force push/deletion; verify before launch |
| P1 | GitHub Dependency Graph | BLOCKED_EXTERNAL | Issue #196 remains open; security workflow uses mandatory Trivy fallback when GitHub differential dependency review is unavailable | Enable Dependency Graph and prove dependency-review executes and blocks a deliberately vulnerable dependency; retain Trivy defense in depth |
| P0 | Launch gate #119 | IN_PROGRESS | Issue #119 is open and remains the authoritative final release gate | Close only after all acceptance evidence is attached and independently verifiable |
| P0 | Production domain/DNS/TLS/Telegram allowed domain | BLOCKED_EXTERNAL | Requires real deployment/domain ownership and provider configuration | Configure and verify live HTTPS, renewal, secure headers and Telegram allowed domain |
| P0 | Production Telegram credentials | BLOCKED_EXTERNAL | Real secret/provider configuration must not be fabricated in repository | Configure through approved secret backend and perform controlled live verification |
| P0 | Production YooKassa credentials/callback | BLOCKED_EXTERNAL | Real financial-provider configuration cannot be proven with CI-only sandbox evidence | Configure secret backend/callback and perform explicitly approved controlled live smoke/reconciliation |
| P0 | Production secret backend/rotation | BLOCKED_EXTERNAL | Requires deployment/admin secret-store configuration | Configure, document ownership/rotation, verify no demo fallback in production |
| P0 | Controlled external-live smoke | BLOCKED_EXTERNAL | Real provider/domain action requires explicit approval; must not be simulated as proof | Execute only after infrastructure/provider prerequisites and record evidence in #119 |
| P1 | CI backup/restore + release rollback automation | DONE | Current CI contains signed PostgreSQL backup/restore and signed full release rollback drills | Do not equate CI drill with production DR; production restore evidence remains a launch operational requirement |
| P1 | Production DR operational proof | PARTIAL | `docs/disaster_recovery.md` exists and CI drills restore/rollback; production RPO/RTO, off-host retention and real restore rehearsal are not fully evidenced here | Define/approve RPO/RTO, backup retention/off-host storage and run a production-like restore rehearsal with evidence |

## Proven lock-order baseline

The following pairs have already been hardened/proven in dedicated work and must not be inverted by later changes:

- `Customer -> Cart`
- `Order -> PaymentCreationAttempt`
- `Order -> Payment`
- `Order -> ReturnRequest` — pending final merge of PR #200 at the time this document was created
- `Order -> FulfillmentTask` — target canonical order; current repository still contains an inversion and therefore this pair is **not yet complete**
- multi-row `ProductVariant` acquisition must remain deterministic by stable sorted/ID order where the code locks multiple variants

This is **not** yet declared a complete global hierarchy. The full repository-wide audit is a separate P1 gate.

## Phase plan and maturity

### Phase 1 — active critical blockers

| Work item | Status | Required maturity |
|---|---|---|
| #201 ingress x/crypto CVE | DONE | L5 repository/CI security evidence |
| #202 ingress gRPC CVE | DONE | L5 repository/CI security evidence |
| #200 refund/return lock order | IN_PROGRESS | L4 concurrency + full CI/Security before merge |
| Fulfillment `Order -> FulfillmentTask` | MISSING | L4 concurrency + full CI/Security before merge |
| Readiness register | IN_PROGRESS | Must remain current on every critical hardening pass |

### Phase 2 — database concurrency

Status: `PARTIAL`.

Required work:

- inventory every `.with_for_update()` / `FOR UPDATE` and write path;
- document lock acquisition edges by endpoint/service;
- verify pairs involving Customer, Cart, Promo, Loyalty, Order, Payment, PaymentCreationAttempt, ReturnRequest, FulfillmentTask, FulfillmentTaskItem, OrderItem, ProductVariant, SLA and outbox/domain rows;
- add deterministic ordering for multi-row locks;
- for every proven inversion: issue -> dedicated branch -> minimal fix -> regression test -> real PostgreSQL concurrency smoke -> full gate.

Exit artifact: `docs/DATABASE_LOCK_ORDER.md`.

### Phase 3 — transaction boundaries and provider safety

Status: `PARTIAL`.

Required work:

- locate external network I/O under active SQLAlchemy transactions;
- use `prepare -> commit -> external call -> fresh-lock finalize` where the operation crosses a provider boundary;
- define explicit connect/read/write/pool timeouts;
- bounded retry + backoff/jitter only for safe/idempotent operations;
- provider idempotency keys and ambiguous-result reconciliation;
- no row locks held while waiting seconds on external providers unless an audited exception is documented.

### Phase 4 — financial integrity

Status: `PARTIAL`.

Payment/refund/cancellation/promo/loyalty must eventually prove:

- no duplicate charge/refund/order;
- immutable monetary order snapshot;
- `Decimal`/`NUMERIC` money semantics and deterministic rounding;
- payment/order/provider amount and currency invariants;
- cumulative refunds cannot exceed successful captured payment;
- ambiguous provider state enters reconciliation/review, never silent success;
- operator-visible reason + immutable audit for manual financial resolution;
- concurrency/failure tests for the last critical race cases.

### Phase 5 — inventory, fulfillment and delivery

Status: `PARTIAL`.

Required launch-safe outcomes:

- no oversell or negative inventory;
- one controlled inventory mutation service/ledger;
- reconciliation for stock/reservations;
- canonical fulfillment state machine and lock order;
- cancelled/refunded orders cannot incorrectly progress through fulfillment;
- shipping/tracking lifecycle consistent with order/fulfillment state;
- operator recovery path for conflicts.

### Phase 6 — security and privacy

Status: `PARTIAL`.

Repository security automation is material and active, but launch still requires completion/verification of:

- admin/customer auth hardening;
- named RBAC permissions and permission matrix;
- PII classification/access/masking/retention controls;
- distributed rate limiting where multiple replicas matter;
- production secret management and rotation;
- branch protection;
- Dependency Graph enablement/differential review;
- real TLS/domain/provider configuration.

### Phase 7 — reliability and durable asynchronous work

Status: `PARTIAL`.

Verify/finish durable webhook intake, outbox/background jobs, lease ownership, retry limits, dead-letter/review queues, manual replay, provider reconciliation and scheduler singleton/fencing behavior. Critical business effects must not exist only in process memory.

### Phase 8 — observability and operations

Status: `PARTIAL`.

Launch-critical paths require structured logs/correlation IDs, metrics, actionable operator errors, review queues, SLOs, alerts and runbooks. Raw log inspection alone is not an acceptable recovery interface for money/inventory failures.

### Phase 9 — release/DR/staging

Status: `PARTIAL`.

CI already exercises substantial Docker/Compose/restore/rollback safety. Remaining work is to prove production-like staging, immutable release artifacts/digests, production secret/infrastructure configuration, operational backup policy and recovery rehearsal.

### Phase 10 — launch gate

Status: `BLOCKED_EXTERNAL` + `IN_PROGRESS`.

Issue #119 remains authoritative. A GO decision is forbidden while any of the following is unresolved:

- critical CI or Security failure;
- open P0 data/money/security defect;
- known critical deadlock on launch path;
- unprotected main/release control required by policy;
- missing production secrets/provider credentials;
- missing domain/TLS/callback configuration;
- unverified backup/restore operational capability;
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
| `docs/PRODUCTION_READINESS_MASTER_PLAN.md` | IN_PROGRESS | This document; update continuously with evidence |
| `docs/DATABASE_LOCK_ORDER.md` | MISSING | Create after current known lock inversions are fixed/audited |
| `docs/IDEMPOTENCY_CONTRACTS.md` | MISSING | Must map checkout/payment/refund/cancellation/webhooks/notification/shipment semantics |
| `docs/RBAC_MATRIX.md` | MISSING | Must be derived from actual named permissions/endpoints |
| `docs/SLO.md` | MISSING | Define measurable production objectives and owners |
| `docs/ERROR_CATALOG.md` | MISSING | Actionable validation/conflict/provider/security/integrity/retry/review taxonomy |
| provider contract docs | PARTIAL | Audit existing provider documentation before adding/claiming completeness |
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
- Enable GitHub Dependency Graph; complete issue #196 acceptance while retaining Trivy.
- Provision production PostgreSQL/Redis/networking and approved secret backend.
- Configure production domain, DNS, TLS and Telegram allowed domain.
- Provision/rotate production Telegram and YooKassa credentials.
- Configure real YooKassa callback and other required provider endpoints.
- Provision operator identities/roles and named launch owner/approver.
- Complete controlled external-live smoke with explicit approval and attach evidence to #119.

## Next execution sequence

1. Finish fresh gates for PR #200; merge only if the exact synchronized head is fully green.
2. Create a dedicated fulfillment lock-order branch from the resulting pilot head.
3. Canonicalize `Order -> FulfillmentTask`, revalidate task ownership and fail with 409 on relationship drift.
4. Add focused regression tests plus real PostgreSQL NOWAIT lock-order proof and wire it into CI.
5. Merge only after full CI + Security + release-safety gates.
6. Build `docs/DATABASE_LOCK_ORDER.md` from a complete repository audit; do not infer a global hierarchy before that audit.
7. Open one PR per newly proven inversion.
8. Perform the repository-wide transaction-boundary audit and remove external I/O from long-lived DB lock scopes.
9. Continue P0 -> P1 work through financial integrity, inventory/fulfillment, security, reliability, observability and release readiness.
10. Keep #119 open until every real launch prerequisite is evidenced.

## Change-control rule for this document

Update this file whenever a launch-critical PR is opened, materially changes scope, is blocked by a new gate, or is merged. Promote status only when the evidence exists. If new information disproves a prior readiness claim, downgrade the status immediately rather than preserving an optimistic historical label.
