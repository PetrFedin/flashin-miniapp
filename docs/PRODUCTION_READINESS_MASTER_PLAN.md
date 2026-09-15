# FLASHIN Production Readiness Master Plan

**Repository:** `PetrFedin/flashin-miniapp`  
**Hardening branch:** `pilot/e2e-hardening-20260808`  
**Current verified pilot head:** `0015a016738d2d4f446cc474d8cea4b6e0fb5b23`  
**Decision:** **NOT READY / NO-GO**  
**Rule:** code existence is not readiness evidence. Promote a status only after the required implementation, tests and gates exist.

## Status vocabulary

- `DONE` — required implementation and stated evidence are complete.
- `PARTIAL` — useful implementation exists but production evidence/recovery is incomplete.
- `MISSING` — required capability/evidence is absent.
- `BLOCKED_EXTERNAL` — requires repository administration, infrastructure, credentials, DNS/provider or other external action.
- `IN_PROGRESS` — dedicated work exists but exact-head gates/merge are not complete.
- `DEFERRED_POST_LAUNCH` — deliberately outside launch-critical scope.

## Launch decision

FLASHIN remains **NO-GO**. The pilot has substantial concurrency, provider-boundary, security and release-safety hardening, but launch remains blocked by unfinished P0/P1 audit work, issue #119, unprotected branch governance, production secrets/providers/domain/TLS and controlled external-live verification.

## Evidence discipline

Launch-critical evidence must be traceable to merged code plus the appropriate unit/integration/concurrency/failure proof, exact-head CI/Security and operational evidence. CI backup/rollback drills do not substitute for real production DR/provider verification.

## Current critical register

| Priority | Area | Status | Evidence / current fact | Exit gate |
|---|---|---|---|---|
| P0 | Caddy x/crypto CVE-2026-56854 | DONE | PR #201 merged with patched dependency and security gates | Keep security contract + Trivy mandatory |
| P0 | Caddy gRPC CVE-2026-84304 | DONE | PR #202 merged after full security/release-safety | Preserve binary/version assertions |
| P0 | Refund/return lock inversion | DONE | PR #200: canonical `Order -> ReturnRequest`, relationship revalidation, PostgreSQL smoke | Preserve invariant |
| P1 | Fulfillment lock inversion | DONE | PR #205: `Order -> FulfillmentTask`, PostgreSQL NOWAIT proof | Preserve invariant |
| P0 | Loyalty checkout lock order | DONE | PR #207: `CrmProfile -> LoyaltyRedemptionHold`, PostgreSQL proof | Preserve invariant |
| P1 | Lock-order evidence registry | DONE | PR #208 created `docs/DATABASE_LOCK_ORDER.md` | Keep registry evidence-based |
| P0 | Cross-order referral deadlock | DONE | PR #210: `ReferralAttribution -> ReferralCode -> CrmProfile(referrer)` with PostgreSQL proof | Preserve shared-referrer ordering |
| P0 | CRM recompute authority | DONE | PR #212: dedicated mutation permission and loyalty ownership separation | Preserve `crm.recompute` boundary |
| P1 | MoySklad outbound DB/provider boundary | DONE | PR #214: snapshot -> end DB transaction -> provider -> fresh finalize | Preserve boundary |
| P1 | Telegram ambiguous delivery | DONE | PR #217: transaction-clean transport; unknown post-send result -> `review_required` | Preserve no-blind-replay contract |
| P1 | Meilisearch DB/provider boundary | DONE | PR #220; exact head `5ccbca9...`, CI #1407, Security #275, merge `cdfdc578...` | Preserve detached snapshot and bounded provider I/O |
| P1 | S3/R2 media DB/provider boundary | DONE | PR #225; exact head `cbc440b...`, CI #1409, Security #278, merge `3b0ee163...` | Preserve prepare -> storage -> fresh auth/RBAC finalize |
| P0 | DeliveryShipment/Order deadlock | DONE | Issue #226 / PR #227; exact head `7771b8408360131819efdb4b43ae7820703f69c3`; CI #1413 and Security #283 success including real PostgreSQL NOWAIT proof, full backend/browser/integrated E2E, Docker, signed backup/restore, signed rollback and production Compose isolation; merged as `0015a016738d2d4f446cc474d8cea4b6e0fb5b23` | Preserve `Order -> DeliveryShipment` |
| P1 | Durable media orphan cleanup | IN_PROGRESS | Issue #222, branch `fix/media-cleanup-recovery-20260908`: durable `object_storage` command, exact generated-key binding, bounded retry/review, operator list/replay, scheduler worker and PostgreSQL smoke implemented; ambiguous `put_object` retains generated key | Exact-head full CI + Security/release-safety and merge required |
| P1 | S3/R2 async event-loop blocking | MISSING | #223: synchronous boto3 remains in async upload request | Dedicated bounded offload/concurrency PR after #222 |
| P1 | Webhook outbox ambiguous post-send replay | MISSING | #224: clean DB/HTTP boundary but ambiguous receiver acceptance may be replayed | Receiver idempotency contract or quarantine ambiguous outcomes |
| P1 | Repository-wide DB lock audit | IN_PROGRESS | Several proven invariants merged; second pass over all `FOR UPDATE` paths incomplete | Continue evidence-first audit; each real inversion separate issue/PR/smoke |
| P1 | External-I/O transaction-boundary audit | PARTIAL | Payment/refund, MoySklad, Telegram, Meilisearch and media boundaries materially hardened; remaining providers not fully certified | Continue provider-by-provider audit |
| P0 | Main branch protection | BLOCKED_EXTERNAL | Launch policy requires protected main | PR requirement + required CI/Security + no force push/deletion |
| P1 | Pilot/release protection | BLOCKED_EXTERNAL | Last verified pilot protection state was `protected:false` | Enable appropriate ruleset/protection before release authority |
| P1 | GitHub Dependency Graph | BLOCKED_EXTERNAL | Issue #196; Trivy fallback remains mandatory | Enable Dependency Graph and differential dependency review |
| P0 | Launch gate #119 | IN_PROGRESS | Authoritative launch checklist remains open | Close only on real evidence |
| P0 | Domain/DNS/TLS/Telegram allowed domain | BLOCKED_EXTERNAL | Requires production infrastructure/provider configuration | Configure and verify HTTPS/renewal/headers/allowed domain |
| P0 | Production Telegram/YooKassa credentials and callbacks | BLOCKED_EXTERNAL | Must not be fabricated in repository | Secret backend + controlled provider verification |
| P0 | Production secret backend/rotation | BLOCKED_EXTERNAL | Requires deployment/admin action | Configure ownership and rotation |
| P0 | Controlled external-live smoke | BLOCKED_EXTERNAL | Real provider action requires explicit approval | Perform only after prerequisites and attach evidence to #119 |
| P1 | CI backup/restore + rollback automation | DONE | Latest merged delivery PR again passed signed backup/restore, signed full rollback and production Compose isolation | Keep mandatory |
| P1 | Production DR operational proof | PARTIAL | CI drills/runbooks exist; production RPO/RTO/off-host rehearsal incomplete | Production-like restore rehearsal and approved RPO/RTO |

## Proven lock-order baseline

Merged pilot invariants currently include:

- `Customer -> Cart`
- `Order -> PaymentCreationAttempt`
- `Order -> Payment`
- `Order -> ReturnRequest`
- `Order -> FulfillmentTask`
- `Order -> DeliveryShipment`
- `CrmProfile -> LoyaltyRedemptionHold`
- `ReferralAttribution -> ReferralCode -> CrmProfile(referrer)`
- deterministic stable ordering for multi-row `ProductVariant` locks

This is not a claimed global hierarchy. `docs/DATABASE_LOCK_ORDER.md` remains the detailed evidence registry.

## Phase status

### 1. Known critical blockers — PARTIAL / ongoing

Merged: #200, #201, #202, #205, #207, #208, #210, #212, #214, #217, #220, #225, #227. No known open P0 from those issues remains, but the wider audit can still discover new P0 defects.

### 2. Database concurrency — IN_PROGRESS

Continue every `.with_for_update()` / raw `FOR UPDATE` and adjacent mutation path. Candidate edges remain candidates until same-row call graph and PostgreSQL proof exist. The loyalty `refund_redeemed_points` local reverse edge remains `POTENTIAL_CYCLE`, not a confirmed defect.

### 3. Transaction boundaries / providers — PARTIAL

Hardened evidence exists for payment/refund, MoySklad outbound, Telegram, Meilisearch and S3/R2 upload. #222 now adds durable media cleanup recovery but remains `IN_PROGRESS` until exact-head gates/merge. #223 and #224 remain separate. Delivery/email/CDN and any remaining provider network paths still require certification.

### 4. Financial integrity — PARTIAL

Continue payment/refund/cancellation/promo/loyalty amount, currency, cumulative-refund and operator-review invariants. No float/mutable historical-money assumption should be accepted without schema/ORM proof.

### 5. Inventory / fulfillment / delivery — PARTIAL

Fulfillment and delivery root lock orders are hardened. Inventory ledger/reconciliation, cancellation/refund interactions and end-to-end shipping consistency still require full launch-grade evidence.

### 6. Security / privacy — PARTIAL

CI security is substantial, but launch still requires complete auth/RBAC/PII/rate-limit review, production secret management, protected branches and real domain/provider configuration.

### 7. Reliability / asynchronous work — PARTIAL

ProviderCommand/outbox/leases/reconciliation exist in several contours. #222 uses the generic durable command substrate but isolates object-storage dispatch from MoySklad. Critical terminal failures must remain visible and manually recoverable; no process-memory-only money/inventory side effect is acceptable.

### 8. Observability / operations — PARTIAL

Continue structured logs/correlation IDs, metrics, SLOs, alerts, review queues and actionable runbooks. Raw logs alone are not a production recovery interface.

### 9. Release / DR / staging — PARTIAL

CI proves substantial Docker build, Compose validation, signed backup/restore and signed rollback behavior. Production-like staging, immutable release evidence and real recovery rehearsal remain incomplete.

### 10. Launch gate — BLOCKED_EXTERNAL + IN_PROGRESS

Issue #119 remains open. GO is prohibited while critical P0/P1 launch risks, required branch governance, production secrets/domain/TLS/provider setup, operator ownership or controlled external-live evidence are incomplete.

## #222 media cleanup recovery contract under review

Current branch implements:

1. generated media keys only: `32 hex + .jpg/.png/.webp`;
2. durable `ProviderCommand` namespace `provider=object_storage`, type `object_storage.media.delete`;
3. SHA-256 aggregate/idempotency binding to the exact generated key;
4. ambiguous S3/R2 write exceptions preserve the generated key through `MediaStorageWriteError`;
5. failed finalize/ambiguous write persists cleanup in a fresh DB phase;
6. scheduler worker claims + commits before delete; storage I/O must see no DB transaction;
7. transient failures use bounded ProviderCommand backoff; permanent configuration/auth codes and malformed/tampered commands enter operator review;
8. `GET /media/cleanup` exposes safe operational status/fingerprint without raw payload or credentials;
9. `POST /media/cleanup/{command_id}/retry` accepts command id only, revalidates binding and audits replay;
10. `scripts/media_cleanup_recovery_smoke.py` is mandatory CI evidence.

Residual truth: a PostgreSQL-backed queue cannot durably record cleanup while PostgreSQL itself is unavailable. The runbook therefore requires storage-vs-DB incident reconciliation after such a dual failure; this branch does not claim impossible durability during database outage.

## Maturity scale

- `L0` absent
- `L1` code
- `L2` unit test
- `L3` integration proof
- `L4` concurrency/failure proof
- `L5` observability/operator recovery/runbook/release gate
- `L6` controlled production verification

Launch-critical contours require at least L5; payment/refund/provider production behavior should reach L6 after explicitly approved live smoke.

## Required authoritative artifacts

| Artifact | Status |
|---|---|
| `docs/PRODUCTION_READINESS_MASTER_PLAN.md` | IN_PROGRESS — synchronized through merged pilot `0015a016...` and active #222 |
| `docs/DATABASE_LOCK_ORDER.md` | IN_PROGRESS — registry exists; audit continues |
| `docs/IDEMPOTENCY_CONTRACTS.md` | MISSING |
| `docs/RBAC_MATRIX.md` | MISSING |
| `docs/SLO.md` | MISSING |
| `docs/ERROR_CATALOG.md` | MISSING |
| provider contracts | PARTIAL |
| operational runbooks | PARTIAL |
| `docs/PRODUCTION_READINESS_FINAL_REPORT.md` | MISSING until final launch pass |

## Pull-request gate

Every hardening PR must state **Problem / Evidence / Root Cause / Fix / Safety / Tests / Rollback**. Never merge while required CI, Security, backup/restore, rollback, Docker or production-isolation checks are red, cancelled, unexpectedly skipped or still running. Merge only the exact reviewed head SHA.

## Next execution sequence

1. Finish #222 exact-head CI/Security/release-safety and merge only on complete success.
2. Close #222 only after merged evidence; do not overclaim database-outage orphan discovery.
3. Address #223 synchronous boto3 event-loop blocking in a separate branch/PR.
4. Address #224 webhook ambiguous post-send replay in a separate branch/PR.
5. Resume repository-wide DB lock and external-I/O audits; every proven risk remains isolated.
6. Continue financial integrity, cancellation, inventory, security, reliability, observability and release readiness by P0 -> P1 -> P2.
7. Keep #119 open until all real launch prerequisites are evidenced.
