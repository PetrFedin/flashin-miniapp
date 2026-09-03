# FLASHIN Production Readiness Master Plan

**Repository:** `PetrFedin/flashin-miniapp`  
**Hardening baseline:** `pilot/e2e-hardening-20260808`  
**Current verified pilot head:** `56ebed25dc15e11ceff9a677cf42bbdd6764c2ea`  
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

The hardening branch now has dedicated, green concurrency proof for the known refund/return and fulfillment root-lock inversions, plus material release/security automation. Launch remains forbidden until the remaining P0/P1 risks are audited and closed, issue #119 acceptance evidence is complete, production prerequisites are configured, protected-branch controls are enabled, and controlled external-live verification is complete.

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
| P0 | Refund/return DB lock ordering | DONE | PR #200 merged into pilot as `5450779bfafd9373e0b1d9b09146e4ff92b2ca8e`; canonical `Order -> ReturnRequest`, relationship revalidation and real PostgreSQL lock-order proof passed full gates | Preserve invariant in repository-wide lock audit |
| P1 | Fulfillment DB lock ordering | DONE | PR #205 exact head `710e3c9ff00bc3e872eb35eb2f34c2fd268ec0c7` passed CI #1384 and Security #243, including PostgreSQL NOWAIT proof, then merged as pilot `56ebed25dc15e11ceff9a677cf42bbdd6764c2ea`; issue #204 closed | Preserve canonical `Order -> FulfillmentTask`; audit child-row interactions separately |
| P1 | Systematic DB lock-order inventory | IN_PROGRESS | Known root pairs are hardened, but no complete authoritative repository-wide lock matrix exists yet | Create/maintain `docs/DATABASE_LOCK_ORDER.md`; audit every `FOR UPDATE` and mutation path; every newly proven inversion gets a separate issue/PR/smoke |
| P1 | External-I/O transaction-boundary audit | PARTIAL | Refund/payment hardening contains prepare/finalize patterns, but repository-wide provider/network audit is not complete | Audit payment, refund, delivery, MoySklad, Telegram, search/storage/notifications; eliminate long external I/O under DB locks |
| P0 | Main branch protection | BLOCKED_EXTERNAL | Fresh repository check: `main` is still `protected:false` | Admin enables PR requirement, required CI/Security, no force push/deletion; verify before launch |
| P1 | Pilot/release branch protection | BLOCKED_EXTERNAL | Fresh repository check: `pilot/e2e-hardening-20260808` is also `protected:false`; code policy is enforced by process/CI, not GitHub branch control | Add appropriate protected-branch/ruleset controls before this branch is used as a release authority |
| P1 | GitHub Dependency Graph | BLOCKED_EXTERNAL | Issue #196 remains open; Security retains mandatory Trivy fallback when GitHub differential dependency review is unavailable | Enable Dependency Graph and prove dependency-review blocks a deliberately vulnerable dependency; retain Trivy defense in depth |
| P0 | Launch gate #119 | IN_PROGRESS | Issue #119 remains open and is the authoritative real-launch gate | Close only after all live/provider/infrastructure/governance evidence is attached and independently verifiable |
| P0 | Production domain/DNS/TLS/Telegram allowed domain | BLOCKED_EXTERNAL | Requires real deployment/domain ownership and provider configuration | Configure and verify public HTTPS, renewal, secure headers and Telegram allowed domain |
| P0 | Production Telegram credentials | BLOCKED_EXTERNAL | Real secret/provider configuration must not be fabricated in repository | Configure through approved secret backend and perform controlled live verification |
| P0 | Production YooKassa credentials/callback | BLOCKED_EXTERNAL | Real financial-provider configuration cannot be proven with CI-only evidence | Configure secret backend/callback and perform explicitly approved controlled live smoke/reconciliation |
| P0 | Production secret backend/rotation | BLOCKED_EXTERNAL | Requires deployment/admin secret-store configuration | Configure, document ownership/rotation and verify fail-closed production validation |
| P0 | Controlled external-live smoke | BLOCKED_EXTERNAL | Real provider/domain action requires explicit approval; must not be simulated as proof | Execute only after infrastructure/provider prerequisites and record evidence in #119 |
| P1 | CI backup/restore + release rollback automation | DONE | CI exercises signed PostgreSQL backup/restore and signed full release rollback drills | Do not equate CI drill with production DR; production restore evidence remains required |
| P1 | Production DR operational proof | PARTIAL | DR documentation and CI drills exist; production RPO/RTO, off-host retention and production-like rehearsal are not yet fully evidenced | Define/approve RPO/RTO, retention/off-host storage and run production-like restore rehearsal |

## Proven lock-order baseline

The following pairs are proven/hardened and must not be inverted by later changes:

- `Customer -> Cart`
- `Order -> PaymentCreationAttempt`
- `Order -> Payment`
- `Order -> ReturnRequest`
- `Order -> FulfillmentTask`
- multi-row `ProductVariant` acquisition must remain deterministic by stable sorted/ID order where multiple variants are locked

This is **not** a complete global hierarchy. A repository-wide audit is now the active P1 concurrency workstream.

## Phase plan and maturity

### Phase 1 — known critical blockers

| Work item | Status | Evidence / maturity |
|---|---|---|
| #201 ingress x/crypto CVE | DONE | L5 repository/CI security evidence |
| #202 ingress gRPC CVE | DONE | L5 repository/CI security evidence |
| #200 refund/return lock order | DONE | L4 concurrency + full CI/Security/release-safety evidence |
| #204/#205 fulfillment `Order -> FulfillmentTask` | DONE | L4 concurrency + full CI/Security/release-safety evidence |
| Readiness register PR #203 | IN_PROGRESS | Must remain current and pass repository gates before merge |

### Phase 2 — database concurrency

Status: `IN_PROGRESS`.

Required work:

- inventory every `.with_for_update()` / `FOR UPDATE` and relevant write path;
- document lock acquisition edges by endpoint/service;
- verify pairs involving Customer, CrmProfile, Cart, CartItem, PromoCode, LoyaltyRedemptionHold/ledger, Order, Payment, PaymentCreationAttempt, ReturnRequest, FulfillmentTask, FulfillmentTaskItem, OrderItem, ProductVariant, SlaEvent, Product/pricing and outbox/domain rows;
- classify child-only mutations separately from root-lock cycles rather than assuming a conflict;
- preserve deterministic multi-row ordering;
- for every proven inversion: issue -> dedicated branch -> minimal fix -> regression test -> real PostgreSQL concurrency smoke -> full gate.

Exit artifact: `docs/DATABASE_LOCK_ORDER.md` with evidence status per edge.

### Phase 3 — transaction boundaries and provider safety

Status: `PARTIAL`.

Required work:

- locate external network I/O under active SQLAlchemy transactions;
- prefer `prepare -> commit -> external call -> fresh-lock finalize` across provider boundaries;
- define explicit connect/read/write/pool timeouts;
- bounded retry + backoff/jitter only for safe/idempotent operations;
- provider idempotency keys and ambiguous-result reconciliation;
- no row locks held while waiting seconds on external providers unless an audited exception is documented.

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

A separate schema/ORM audit is required because current ORM models still contain SQLAlchemy `Float` declarations for monetary fields while migration `0032_fixed_precision_money.py` may have changed physical PostgreSQL types. No conclusion is promoted until actual ORM-to-schema consistency is verified.

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

Repository security automation is material and active, but launch still requires completion/verification of:

- admin/customer auth hardening;
- named RBAC permissions and permission matrix;
- PII classification/access/masking/retention controls;
- distributed rate limiting where multiple replicas matter;
- production secret management and rotation;
- branch protection/rulesets;
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
| `docs/PRODUCTION_READINESS_MASTER_PLAN.md` | IN_PROGRESS | PR #203; update continuously with evidence |
| `docs/DATABASE_LOCK_ORDER.md` | MISSING | Active next P1 artifact; build from exhaustive repository audit |
| `docs/IDEMPOTENCY_CONTRACTS.md` | MISSING | Must map checkout/payment/refund/cancellation/webhooks/notification/shipment semantics |
| `docs/RBAC_MATRIX.md` | MISSING | Must be derived from actual named permissions/endpoints |
| `docs/SLO.md` | MISSING | Define measurable production objectives and owners |
| `docs/ERROR_CATALOG.md` | MISSING | Actionable validation/conflict/provider/security/integrity/retry/review taxonomy |
| provider contract docs | PARTIAL | Audit existing provider documentation before claiming completeness |
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

1. Merge this readiness update only after fresh exact-head CI + Security gates.
2. Build `docs/DATABASE_LOCK_ORDER.md` from a repository-wide inventory of every `FOR UPDATE` and relevant mutation path.
3. Classify each lock edge as proven-safe, one-direction-only, potential cycle, or missing evidence; do not invent a global hierarchy.
4. For each newly proven inversion, open an issue and a dedicated minimal branch/PR with regression + real PostgreSQL concurrency proof.
5. After known lock cycles are closed, perform the repository-wide transaction-boundary audit and remove external I/O from long-lived DB lock scopes.
6. Audit ORM money types against the actual migrated PostgreSQL schema before changing financial types; separate schema corrections into focused PRs.
7. Continue P0 -> P1 through financial integrity, inventory/fulfillment, security, reliability, observability and release readiness.
8. Keep #119 open until every real launch prerequisite is evidenced.

## Change-control rule for this document

Update this file whenever a launch-critical PR is opened, materially changes scope, is blocked by a new gate, or is merged. Promote status only when the evidence exists. If new information disproves a prior readiness claim, downgrade the status immediately rather than preserving an optimistic historical label.
