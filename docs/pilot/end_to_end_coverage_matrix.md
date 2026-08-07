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
| Payment creation | Order -> payment service -> provider -> persisted payment | Real `/api/payments` and domain settlement run in `integrated-e2e`; only external YooKassa HTTP is replaced by a local deterministic boundary | PARTIAL | YooKassa sandbox credentials, real redirect and provider-side payment evidence |
| Payment callback | Provider webhook -> idempotent effect -> paid/review | Payment idempotency, reconciliation and circuit-breaker tests | PASS | One real duplicate YooKassa sandbox webhook in pilot environment |
| Payment return | Provider return -> polling -> order view | Frontend return-route tests/browser polling plus integrated paid-state persistence | PARTIAL | Deployed YooKassa return URL with real sandbox payment |
| Order history | Profile/orders -> details/status | Backend API tests, mocked browser navigation and integrated final order refresh | PASS | Repeat against deployed customer history |
| Order cancellation | Eligible order -> cancel -> stock/promo/loyalty reversal | Transactional cancellation smoke plus customer/Admin browser paths | PASS | Pilot stock and loyalty reversal evidence |
| Returns | Customer request -> Admin -> provider result | Backend return/reconciliation tests plus customer/Admin browser round trip | PASS | Real YooKassa sandbox refund + reconciliation |
| Partial refunds | Review -> partial refund -> remaining refundable amount | Cumulative refund smoke and Admin terminal-state browser path | PASS | Real provider sandbox partial refund |
| Support | Customer ticket -> named Admin owner -> status/priority | Admin-only ownership/state-machine tests and browser assignment/update | PASS | Map active Admin IDs to named pilot owners; notification/SLA evidence |
| Privacy | Customer export/request -> Admin -> terminal result | Backend privacy/idempotency tests and browser workflow | PASS | Legal retention review and one deployed controlled request |
| Notifications | Domain event -> outbox -> lease -> send/retry | Notification lease/retry tests and smoke | PASS | Telegram sandbox/live delivery evidence |
| Business events | Failed event -> diagnosis -> replay -> queue | Worker/recovery smokes and Admin browser recovery/replay | PASS | Observe deployed replay and poison-event alert |
| Webhooks | Event -> outbox -> leased delivery/retry | Webhook lease/integrity smokes | PASS | External receiver sandbox evidence |
| Scheduler | Scheduled job -> distributed lock -> one execution | Scheduler lock smoke | PASS | Observe on deployed pilot |
| Stock sync | MoySklad -> mapping -> local product/variant/stock | Backend sync tests | PARTIAL | Token plus 5-10 real products/variants and retained sync evidence |
| Search index | Product change -> Meilisearch -> storefront search | Backend search tests and production graph | PARTIAL | Production/pilot key and index rebuild evidence |
| Media | Upload -> R2/S3 -> CDN -> purge | Backend media tests and production graph | PARTIAL | R2/S3/CDN credentials and public delivery evidence |
| Admin authentication | Login -> protected sections -> logout | Admin security tests/build, mocked browser login/logout and real Admin login in `integrated-e2e` | PASS | Deployed expiry and permission-denied assertions |
| Admin products/promos | Create promo/product -> CSV import/export -> refreshed lists | Admin tests and stateful browser mutations/download | PASS | Deployed database + audit log evidence |
| Admin inventory | Low stock -> snapshot -> abandoned carts -> notifications | Backend operations tests and browser list/action coverage | PASS | Live inventory + worker evidence |
| Admin order fulfillment | Paid order -> pick -> pack -> ready -> shipment -> tracking -> shipped -> delivered | PostgreSQL fulfillment smoke, mocked Admin lifecycle and `integrated-e2e` real Admin UI mutating the same persisted paid order created by Mini App | PASS | One controlled real courier/pickup order and shipment evidence |
| Admin service operations | Support/privacy/returns -> operator -> terminal state | Domain rules plus browser processing | PASS | Deployed RBAC and named owner roster |
| Admin pilot operations | Protected status -> GO/NO-GO -> integrity/money attention | Observability tests, metrics/Grafana and browser-valid contract | PASS | Deployed dashboard/access/external alerts |
| Runtime pilot guard | Allowlist -> signed evidence/admission -> first 20 -> STOP | Fail-closed signed admission/runtime, lock/fsync/lineage/DB-anchor and first-20 tests | PASS | Fresh live provider/governance evidence then controlled pilot arm |
| Monitoring | Metrics -> rules -> Grafana | Monitoring config/capability and production Compose gate | PASS | External receiver and named on-call owner |
| Backup/restore integrity | pg_dump -> signed manifest -> isolated verify -> destructive restore | Mandatory CI drill with signature/SHA/schema/content checks | PASS | Repeat on pilot host/storage and retain artifacts externally |
| Release rollback mechanics | current/previous release + backup -> STOP -> restore -> health -> promotion | Mandatory full rollback CI drill through production rollback path | PASS | Production-like host drill with RTO/RPO |
| Repository governance | protected main -> PR-only -> strict required checks -> exact push CI -> signed report | v19 validates classic/ruleset protection, exact release, App source and successful push CI; admission fails closed without the six mandatory v20 checks | PARTIAL | `main` is currently unprotected; configure protection/ruleset then create fresh signed report |
| Mocked browser E2E | Mini App/Admin browser journeys -> deterministic API fixtures | `browser-e2e` Playwright job with failure traces/screenshots/video | PASS | Keep mandatory; it is UI contract evidence, not provider evidence |
| Integrated internal-stack E2E | signed test Telegram payload -> Mini App -> FastAPI -> PostgreSQL -> payment domain -> Admin fulfillment/delivery -> Mini App refresh | `integrated-e2e` uses no Playwright API route mocks; real application/database internals, only external YooKassa HTTP boundary replaced | PASS | Keep as required CI check; do not represent it as live YooKassa/Telegram evidence |
| Production Compose isolation | exact CI release -> production Compose validation | `docker` depends on all five preceding application jobs including `integrated-e2e`; build, Caddy, backup/restore, rollback and isolation all pass | PASS | Repeat deployment/readiness on pilot host |
| Database-bound first-20 evidence | signed scenario -> exact order/payment/refund/inventory row -> final 20-slot equality | Capability v16 fails closed on fabricated, unrelated, missing or drifted DB evidence | PASS | Populate only from actual first-20 pilot orders |
| Durable inventory evidence | checkout reserve -> movement ledger -> payment commit/cancel release -> signed verification | Capability v17 checks quantities, sequence, continuity and order-compatible terminal movement | PASS | Reconcile actual first-20 inventory movements |

## Mandatory browser layers

FLASHIN now has **two separate browser evidence layers** and both are required:

1. `browser-e2e` — broad UI regression journeys using deterministic stateful API fixtures. It efficiently proves routing, controls, validations and UI/API contracts across Mini App and Admin.
2. `integrated-e2e` — one critical stateful order path with no `page.route(...)` API mocks. It starts PostgreSQL, applies the current Alembic head, starts the real FastAPI application plus real Mini App/Admin, authenticates with a correctly signed test Telegram payload, creates and pays a real persisted order, completes real Admin fulfillment/shipment/delivery, then reloads the same order in Mini App.

The integrated wrapper is test-only and fails to boot unless `APP_ENV` is `test`/`ci` and `INTEGRATED_E2E=true`. It is not wired to production Compose. The only replaced internal-to-external boundary is YooKassa HTTP; the FLASHIN payment route, persistence and settlement logic remain real.

## Transactional and infrastructure evidence

The backend CI suite separately runs real PostgreSQL smokes for customer journey, referral attribution, cancellation, fulfillment, payment review, cumulative refunds, business-event recovery, webhook leases, notification leases, scheduler locking and refund reconciliation.

Every new PostgreSQL backup has a signed manifest binding archive SHA/size, Alembic revision, schema fingerprint and critical table content fingerprints. The mandatory restore drill rejects tampering and proves destructive restoration of a known sentinel.

The full release rollback drill builds different immutable current/previous releases, creates a signed restore-proven backup, invokes the real rollback path, proves runtime STOP, database restoration, health, service restart and release pointer promotion. External providers are deliberately not called in CI.

## Evidence boundary

The exact v20 `main` release `39c9faa0309bcf5ce669ec246d10761e47108a87` completed successful `push` CI run `31173096787` with all six required jobs:

- `backend`
- `frontend`
- `admin`
- `browser-e2e`
- `integrated-e2e`
- `docker`

This is strong **internal-stack and release-mechanics evidence**. It is not authorization for real money.

The following remain external/deployed admission requirements: real Telegram signatures, YooKassa redirect/webhook/refund/reconciliation, MoySklad sync, Telegram notification delivery, Meilisearch when enabled, R2/CDN when enabled, public DNS/HTTPS, external alert receiver, named owners, legal documents, repository branch protection, production-like retained backup/rollback evidence and completion of all P01-P20 live runner steps.

## Admission rule

A process marked `PARTIAL`, `BLOCKED` or `NOT COVERED` cannot be represented as fully proven. A `PASS` row is scoped to the evidence named in that row. Pilot `GO` requires the signed live lifecycle, signed repository-governance evidence for the protected exact release, final admission verification and the explicit allowlisted 20-order runtime contract.
