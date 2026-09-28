# FLASHIN pilot end-to-end coverage matrix

Status values: `PASS`, `PARTIAL`, `BLOCKED`, `NOT COVERED`.

`PASS` means the stated repository/CI path is proven. It does **not** imply a live third-party provider or deployed host is proven unless the evidence column explicitly says so.

## Process matrix

| Process | End-to-end path | Current automated evidence | Status | Remaining pilot evidence |
|---|---|---|---|---|
| Telegram authentication | Telegram init data -> backend auth -> customer session | Backend trust-domain tests; mocked browser bootstrap; `integrated-e2e` sends a correctly HMAC-signed test WebApp payload through the real `/api/auth/telegram` route and database | PARTIAL | One deployed Mini App session with real Telegram-signed init data |
| Storefront bootstrap | Auth -> catalog -> cart -> looks -> wishlist | Frontend tests/build, mocked Playwright journeys and real-stack `integrated-e2e` storefront load against FastAPI/PostgreSQL | PASS | Repeat on public pilot domains |
| Product discovery | Catalog -> search -> product card -> details | API/frontend tests and browser navigation | PASS | Verify production Meilisearch index when enabled |
| Product variants | Product -> size/variant -> stock -> restock | Backend stock tests and browser variant/restock states; integrated E2E selects real seeded stock from PostgreSQL | PASS | Confirm after live MoySklad synchronization |
| Wishlist | Product -> wishlist -> profile -> remove | Backend endpoints and browser round trip | PASS | Repeat in deployed persistent customer session |
| Cart | Product -> cart -> quantity/update/remove | Transactional smoke, browser mutations, integrated E2E real cart | PASS | Repeat against pilot inventory |
| Promotions | Cart -> promo -> recalculated totals | Backend pricing constraints, browser promo path, integrated E2E real `FLASH10` promo | PASS | Deployed invalid/expired promo evidence |
| Loyalty | Reserve points -> order -> cancel/refund reversal | Transactional smokes and browser points/cancellation paths | PASS | Deployed balance verification after cancellation/refund |
| Referral | Referral code -> first paid order -> inviter reward | PostgreSQL referral smoke, duplicate-webhook proof, late-attribution rejection and browser profile/cart path | PASS | Two controlled pilot customers plus ledger evidence |
| Checkout | Cart -> delivery form -> order | Idempotency tests, transactional journey and integrated E2E real `/api/orders/checkout` against PostgreSQL | PASS | Repeat in deployed Telegram Mini App |
| Payment creation | Order -> payment service -> provider -> persisted payment | Real `/api/payments` and domain settlement run in `integrated-e2e`; only external YooKassa HTTP is replaced by a local deterministic boundary | PARTIAL | YooKassa sandbox/live credentials, real redirect and provider-side payment evidence |
| Payment callback | YooKassa -> canonical `/api/webhooks/yookassa` -> authoritative provider read -> idempotent paid/review effect | `integrated-e2e` sends the canonical callback twice after provider-state transition; payment idempotency, reconciliation and circuit-breaker tests cover duplicate and recovery paths; legacy payment/refund callback routes remain registered during migration | PASS | One real duplicate YooKassa sandbox/live webhook in pilot environment |
| Missed payment callback recovery | persisted latest YooKassa attempt -> scheduler -> authoritative provider GET -> payment settlement/cancellation/review -> downstream effects | Mandatory `payment_reconciliation_recovery_smoke.py` creates a real PostgreSQL checkout and pending payment, deliberately sends no webhook, changes only the deterministic provider state to `succeeded`, then proves automatic paid settlement, stock commit, fulfillment creation, one paid notification and one durable MoySklad customer-order command; `PaymentEvent` remains absent and replay creates no duplicate effects | PASS | Observe the scheduler recover one controlled sandbox/live payment whose callback is intentionally unavailable or delayed, retaining provider and database evidence |
| Payment return | Provider return -> `/payment-result` -> polling -> order view | Frontend return-route tests/browser polling plus integrated redirect back to the real Mini App route after payment confirmation | PARTIAL | Deployed YooKassa return URL with real sandbox/live payment |
| Order history | Profile/orders -> details/status | Backend API tests, mocked browser navigation and integrated final order refresh | PASS | Repeat against deployed customer history |
| Order cancellation | Eligible order -> cancel -> stock/promo/loyalty reversal | Transactional cancellation smoke plus customer/Admin browser paths | PASS | Pilot stock and loyalty reversal evidence |
| Returns | Customer request -> Admin -> refund creation -> provider success callback -> terminal state | `integrated-e2e` creates the customer return, requests a full Admin refund, keeps the provider refund pending, then sends duplicate `refund.succeeded` callbacks through `/api/webhooks/yookassa` and verifies one terminal effect | PASS | Real YooKassa sandbox/live refund + reconciliation evidence |
| Partial refunds | Review -> partial refund -> remaining refundable amount | Cumulative refund smoke and Admin terminal-state browser path | PASS | Real provider sandbox/live partial refund |
| Stock restoration after refund | paid stock commit -> full refund -> return inventory movement -> stock restored exactly once | `integrated-e2e` verifies final stock returns to the seeded quantity and exactly one `return` movement exists despite duplicate refund callbacks | PASS | Compare the same SKU against live MoySklad after a controlled refund |
| Support | Customer ticket -> named Admin owner -> status/priority | Admin-only ownership/state-machine tests and browser assignment/update | PASS | Map active Admin IDs to named pilot owners; notification/SLA evidence |
| Privacy | Customer export/request -> Admin -> terminal result | Backend privacy/idempotency tests and browser workflow | PASS | Legal retention review and one deployed controlled request |
| Notifications | Domain event -> durable notification -> lease -> Telegram transport -> send/retry | Notification lease/retry tests and PostgreSQL lease smoke; `notification_transport_smoke.py` runs the real `send_pending_batch` adapter through claim/lease/finalize and verifies the exact `send_message` handoff plus no resend after `sent`; `integrated-e2e` verifies paid/refund notifications are persisted exactly once | PASS | Telegram sandbox/live delivery evidence from the deployed worker and Bot API |
| Business events | Failed event -> diagnosis -> replay -> queue | Worker/recovery smokes and Admin browser recovery/replay | PASS | Observe deployed replay and poison-event alert |
| Webhooks | Event -> outbox -> leased delivery/retry | Webhook lease/integrity smokes | PASS | External receiver sandbox evidence |
| Scheduler | Scheduled job -> distributed lock -> one execution | Scheduler lock smoke; payment reconciliation is registered as a locked async job every two minutes and refund reconciliation every five minutes | PASS | Observe both reconciliation jobs on deployed pilot |
| Stock sync | MoySklad -> mapping -> local product/variant/stock | Backend sync tests | PARTIAL | Token plus 5-10 real products/variants and retained sync evidence |
| MoySklad outbound documents | paid order -> `customerorder`; shipped order -> `demand`; refunded order -> `salesreturn` | Mandatory backend CI provider-spine smoke uses real PostgreSQL and the real provider-command worker, replacing only MoySklad HTTP; it verifies command dispatch, `external_id` persistence and document payload/link relationships for all three document types | PASS | Live MoySklad token, organization/agent/store IDs and retained provider-side document IDs |
| Search index | Product change -> Meilisearch -> storefront search | Backend search tests and production graph | PARTIAL | Production/pilot key and index rebuild evidence |
| Media | Upload -> R2/S3 -> CDN -> purge | Backend media tests and production graph | PARTIAL | R2/S3/CDN credentials and public delivery evidence |
| Admin authentication | Login -> protected sections -> logout | Admin security tests/build, mocked browser login/logout and real Admin login in `integrated-e2e` | PASS | Deployed expiry and permission-denied assertions |
| Admin products/promos | Create promo/product -> CSV import/export -> refreshed lists | Admin tests and stateful browser mutations/download | PASS | Deployed database + audit log evidence |
| Admin inventory | Low stock -> snapshot -> abandoned carts -> notifications | Backend operations tests and browser list/action coverage | PASS | Live inventory + worker evidence |
| Admin order fulfillment | Paid order -> ready -> picked -> packed -> shipped -> delivered/completed | PostgreSQL fulfillment smoke, mocked Admin lifecycle and `integrated-e2e` real Admin UI mutating the same persisted paid order created by Mini App; shipment queues the MoySklad `demand` command | PASS | One controlled real courier/pickup order and shipment evidence |
| Admin service operations | Support/privacy/returns -> operator -> terminal state | Domain rules plus browser processing | PASS | Deployed RBAC and named owner roster |
| Admin pilot operations | Protected status -> GO/NO-GO -> integrity/money attention | Observability tests, metrics/Grafana and browser-valid contract | PASS | Deployed dashboard/access/external alerts |
| Runtime pilot guard | Allowlist -> signed evidence/admission -> first 20 -> STOP | Fail-closed signed admission/runtime, lock/fsync/lineage/DB-anchor and first-20 tests | PASS | Fresh live provider/governance evidence then controlled pilot arm |
| Monitoring | Metrics -> rules -> Grafana | Monitoring config/capability and production Compose gate | PASS | External receiver and named on-call owner |
| Backup/restore integrity | pg_dump -> signed manifest -> isolated verify -> destructive restore | Mandatory CI drill with signature/SHA/schema/content checks | PASS | Repeat on pilot host/storage and retain artifacts externally |
| Release rollback mechanics | current/previous release + backup -> STOP -> restore -> health -> promotion | Mandatory full rollback CI drill through production rollback path | PASS | Production-like host drill with RTO/RPO |
| Repository governance | protected main -> PR-only -> strict required checks -> exact CI -> signed report | Governance tooling now defaults fail-closed to all six checks: `backend`, `frontend`, `admin`, `browser-e2e`, `integrated-e2e`, `docker`; configuration script can apply the exact policy with a privileged operator token | PARTIAL | `main` must be protected with that policy, then a fresh signed governance report must bind the exact release |
| Mocked browser E2E | Mini App/Admin browser journeys -> deterministic API fixtures | `browser-e2e` Playwright job with failure traces/screenshots/video | PASS | Keep mandatory; it is UI contract evidence, not provider evidence |
| Integrated internal-stack E2E | signed test Telegram payload -> Mini App -> FastAPI/PostgreSQL -> payment -> canonical YooKassa callback -> stock -> Admin fulfillment/delivery -> return/refund callback -> stock restore -> notification -> terminal Mini App/Admin state | `integrated-e2e` uses no Playwright API route mocks; application/database internals are real. The test-only YooKassa boundary transitions authoritative provider state and calls the canonical webhook twice for payment and refund idempotency. The same persisted order is verified through full refund, restored stock, queued MoySklad `customer_order`/`demand`/`sales_return` commands and one refund notification | PASS | Keep as a required CI check; do not represent the deterministic YooKassa boundary, queued MoySklad commands or persisted notifications as live provider evidence |
| Production Compose isolation | exact CI release -> production Compose validation | `docker` depends on all five preceding application jobs including `integrated-e2e`; build, Caddy, backup/restore, rollback and isolation are mandatory | PASS | Repeat deployment/readiness on pilot host |
| Database-bound first-20 evidence | signed scenario -> exact order/payment/refund/inventory row -> final 20-slot equality | Capability v16 fails closed on fabricated, unrelated, missing or drifted DB evidence | PASS | Populate only from actual first-20 pilot orders |
| Durable inventory evidence | checkout reserve -> movement ledger -> payment commit/cancel release -> signed verification | Capability v17 checks quantities, sequence, continuity and order-compatible terminal movement | PASS | Reconcile actual first-20 inventory movements |

## Mandatory browser layers

FLASHIN has **two separate browser evidence layers** and both are required:

1. `browser-e2e` — broad UI regression journeys using deterministic stateful API fixtures. It proves routing, controls, validations and UI/API contracts across Mini App and Admin.
2. `integrated-e2e` — the critical real-stack order lifecycle with no `page.route(...)` application API mocks. It starts PostgreSQL, applies the current Alembic head, starts the real FastAPI application plus real Mini App/Admin, authenticates with a correctly signed test Telegram payload, creates and pays a persisted order, receives the canonical payment callback twice, fulfills and delivers the order, creates a return, requests a full refund, receives the canonical refund callback twice, restores stock exactly once, persists the refund notification, and verifies refunded state in both Mini App and Admin.

The integrated wrapper is test-only and fails to boot unless `APP_ENV` is `test`/`ci` and `INTEGRATED_E2E=true`. It is not wired to production Compose. The replaced browser-lifecycle provider boundary is YooKassa HTTP; the FLASHIN auth/payment/webhook/order/inventory/fulfillment/return/refund/notification persistence paths remain real.

MoySklad outbound execution is proven separately by the mandatory `provider_integration_spine_smoke.py` backend CI step: it uses the real PostgreSQL provider-command lifecycle and worker for `customerorder`, `demand` and `salesreturn`, replacing only the remote MoySklad HTTP boundary.

The callback-loss recovery path is proven separately by `payment_reconciliation_recovery_smoke.py`: no payment webhook is sent, the authoritative provider state is changed to succeeded, and the scheduler job's domain path settles the order exactly once through inventory, fulfillment, notification persistence and MoySklad command enqueue.

The Telegram worker adapter is proven by `notification_transport_smoke.py`: the real PostgreSQL delivery lease path reaches the real `send_pending_batch` code and an isolated Bot-like transport receives the exact `send_message` call; a replay after `sent` produces no second send.

## Browser journeys

Nine stateful Playwright journeys remain the broad deterministic UI regression baseline from the immutable v17 release capability. The `integrated-e2e` journey is an additional real-stack gate, not a replacement for those browser contracts.

The service-operation browser evidence still binds actions to an accountable active Admin ID, and fulfillment evidence still verifies the full picklist before ready/shipment transitions. These phrases remain explicit because immutable release archives verify their capability markers during every full backend run.

## Transactional referral evidence

The PostgreSQL referral smoke proves the invariant `first paid order -> one inviter reward`, rejects late attribution, and prevents duplicate provider callbacks from duplicating referral rewards.

## Signed backup and restore evidence

The signed backup/restore gate binds archive SHA/size, Alembic revision, schema fingerprint and critical-table content fingerprints, rejects tampering, and proves an isolated destructive restore before release rollback is allowed.

## Transactional and infrastructure evidence

The backend CI suite separately runs real PostgreSQL smokes for the provider integration spine, customer journey, referral attribution, cancellation, fulfillment, payment review, missed-payment callback reconciliation, cumulative refunds, business-event recovery, webhook leases, notification leases, Telegram notification transport, scheduler locking and refund reconciliation.

Every new PostgreSQL backup has a signed manifest binding archive SHA/size, Alembic revision, schema fingerprint and critical table content fingerprints. The mandatory restore drill rejects tampering and proves destructive restoration of a known sentinel.

The full release rollback drill builds different immutable current/previous releases, creates a signed restore-proven backup, invokes the real rollback path, proves runtime STOP, database restoration, health, service restart and release pointer promotion. External providers are deliberately not called in CI.

## Evidence boundary

Repository CI is strong **internal-stack, provider-adapter and release-mechanics evidence**. It is not authorization for real money.

The following remain external/deployed admission requirements: real Telegram signatures, YooKassa redirect/webhook/refund/reconciliation, live MoySklad synchronization and outbound documents, Telegram notification delivery, Meilisearch when enabled, R2/CDN when enabled, public DNS/HTTPS, external alert receiver, named owners, legal documents, protected `main`, production-like retained backup/rollback evidence and completion of all P01-P20 live runner steps.

Use the guarded real runners only against the deployed pilot environment:

- `RUN_REAL_E2E=1` — `backend/tests/e2e/test_real_order_flow_runner.py` for the live creation/payment/fulfillment path.
- `RUN_REAL_LIFECYCLE_E2E=1` — `backend/tests/e2e/test_order_payment_refund_flow.py` for terminal order/payment/delivery/return/stock/refund-notification/diagnostics verification.

## Admission rule

A process marked `PARTIAL`, `BLOCKED` or `NOT COVERED` cannot be represented as fully proven. A `PASS` row is scoped to the evidence named in that row. Pilot `GO` requires provider wiring preflight, live provider probes/lifecycle evidence, signed repository-governance evidence for the protected exact release, final admission verification and the explicit allowlisted 20-order runtime contract.
