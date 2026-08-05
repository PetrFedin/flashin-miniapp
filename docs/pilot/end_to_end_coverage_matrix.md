# FLASHIN pilot end-to-end coverage matrix

Status values: `PASS`, `PARTIAL`, `BLOCKED`, `NOT COVERED`.

| Process | Customer / operator path | Current automated evidence | Status | Pilot action |
|---|---|---|---|---|
| Telegram authentication | Telegram init data -> backend auth -> customer session | Backend auth/trust-domain tests plus Playwright Telegram WebApp bootstrap | PARTIAL | Prove one deployed Mini App session with real signed Telegram init data |
| Storefront bootstrap | Auth -> catalog -> cart -> looks -> wishlist | Frontend loader tests, production build and Playwright catalog/looks bootstrap | PASS | Repeat against deployed pilot API and Telegram domain |
| Product discovery | Catalog -> search -> product card -> product details | API/frontend tests plus Playwright search and catalog-to-product navigation | PASS | Verify deployed search with production Meilisearch index |
| Product variants | Product -> size/variant -> stock availability -> restock subscription | Backend stock tests plus browser in-stock selection, out-of-stock restock and size helper | PASS | Confirm the same states after live MoySklad synchronization |
| Wishlist | Product -> add/remove wishlist -> profile wishlist | Backend endpoints plus browser add, profile load and removal round trip | PASS | Repeat against deployed persistent customer session |
| Cart | Product -> cart -> quantity update/remove | Transactional smoke plus browser add, increment, decrement and removal | PASS | Repeat against deployed inventory reservations |
| Promotions | Cart -> promo validation -> recalculated totals | Backend constraints/checkout tests plus browser successful promo path | PASS | Add deployed invalid/expired promo evidence |
| Loyalty | Cart -> reserve points -> order -> cancellation/refund reversal | Transactional smokes plus browser points reservation and customer cancellation | PASS | Verify deployed balance after cancellation/refund |
| Referral | Profile/referral code -> cart attribution -> first paid order -> one inviter reward | PostgreSQL transactional referral smoke, duplicate-webhook proof, late-attribution rejection, backend tests and browser profile/code/cart path | PASS | Repeat with two controlled pilot customers and retain order/loyalty ledger evidence |
| Checkout | Cart -> delivery form -> order creation | Transactional journey, idempotency tests and browser checkout/order creation | PASS | Repeat through Telegram MainButton in deployed Mini App |
| Payment creation | Order -> YooKassa payment creation -> redirect | Payment service/review tests plus browser failure-safe order fallback | PARTIAL | Requires YooKassa test credentials and successful redirect evidence |
| Payment callback | Provider webhook -> idempotent domain effect -> paid/review state | Payment idempotency, reconciliation and circuit-breaker tests | PASS | Prove one real duplicate test webhook in pilot environment |
| Payment return | Provider return URL -> order polling -> orders view | Frontend rules, backend status tests and Playwright paid return-route polling | PARTIAL | Prove deployed YooKassa return URL with real sandbox payment |
| Order history | Profile/orders -> order details/status | Backend API tests plus browser profile/orders navigation and refreshed state | PASS | Repeat against deployed customer history |
| Order cancellation | Eligible order -> cancel -> stock/promo/loyalty reversal | Transactional cancellation smoke plus customer and Admin browser cancellation | PASS | Verify deployed stock and loyalty reversal evidence |
| Returns | Customer request -> Admin queue -> validated amount -> provider result | Backend return/reconciliation tests plus customer registration and Admin approval browser round trip | PASS | Prove one real YooKassa sandbox refund and reconciliation |
| Partial refunds | Review -> partial provider refund -> remaining refundable balance | Cumulative refund smoke plus Admin partial-refund browser terminal state | PASS | Prove real provider sandbox partial refund |
| Support | Customer ticket -> accountable Admin owner -> priority/status transition | Backend Admin-only ownership schema, state-machine tests and browser assignment/update round trip | PASS | Map active Admin IDs to named pilot owners and verify Telegram SLA notification |
| Privacy | Customer export/request -> Admin process -> terminal result | Backend privacy/idempotency tests plus browser export/request/Admin process round trip | PASS | Complete deployed legal retention review and one controlled request |
| Notifications | Domain event -> notification outbox -> lease -> send/retry | Notification lease smoke and retry-state tests | PASS | Prove Telegram sandbox delivery |
| Business events | Failed event -> diagnosis -> confirmed replay -> pending queue | Worker/recovery smokes plus Admin browser recovery/replay journey | PASS | Observe replay processing and poison-event alert in deployed environment |
| Webhooks | Event -> destination outbox -> leased delivery/retry | Webhook lease smoke and integrity tests | PASS | Add external receiver sandbox evidence |
| Scheduler | Scheduled job -> distributed lock -> one execution | Scheduler lock smoke | PASS | Observe scheduled execution in deployed pilot |
| Stock sync | MoySklad -> mapping -> local product/variant/stock | Backend sync tests | PARTIAL | Requires token and 5-10 real products/variants |
| Search index | Product changes -> Meilisearch indexing -> storefront search | Backend search tests and production graph | PARTIAL | Requires production key and index rebuild evidence |
| Media | Upload -> object storage -> public delivery -> purge | Backend media tests and production graph | PARTIAL | Requires R2/S3/CDN credentials and live evidence |
| Admin authentication | Login -> protected sections -> logout | Admin security tests/build plus Playwright login/logout | PASS | Add deployed session-expiry and permission-denied assertions |
| Admin product/promo operations | Promo create -> product create -> CSV import/export -> refreshed lists | Admin tests plus stateful browser mutations and downloaded export | PASS | Repeat with deployed database and audit-log evidence |
| Admin inventory operations | Low stock -> snapshot -> abandoned carts -> notification queue | Backend operations tests plus browser status/list/POST evidence | PASS | Repeat with live inventory and notification worker |
| Admin order operations | Paid order -> full picklist -> packed -> ready -> shipment -> shipped -> delivered/completed | PostgreSQL transactional fulfillment smoke plus stateful Admin browser lifecycle, SLA, ownership, notification and audit assertions | PASS | Repeat with one controlled courier/pickup pilot order and retain shipment evidence |
| Admin service operations | Support ownership, privacy and returns queues -> operator action -> terminal state | Domain-rule unit tests plus browser assignment, privacy and partial refund processing | PASS | Validate deployed RBAC roles and named owners against the pilot roster |
| Admin pilot operations | Protected status -> GO/NO-GO -> integrity/money attention | Observability tests, metrics/Grafana and browser-valid GO contract | PASS | Verify deployed dashboard, access control and external alerts |
| Runtime pilot guard | Allowlist -> first 20 orders -> automatic STOP | Pilot runtime and circuit-breaker tests | PASS | Requires signed admission and controlled live run |
| Monitoring | Metrics -> Prometheus rules -> Grafana dashboard | Monitoring config/capability tests and production Compose gate | PASS | External receiver and named on-call owner still required |
| Backup/restore integrity | pg_dump -> signed manifest -> isolated verify -> destructive restore -> exact ledger recovery | Unit tests plus mandatory Docker Compose drill with SHA/signature tamper rejection, Alembic/schema fingerprints, critical table digests and restored sentinel | PASS | Run the same command against production-like storage and retain backup/manifest artifacts |
| Release rollback mechanics | Different current/previous signed releases + verified backup -> runtime STOP -> code/database restore -> health -> pointer promotion -> signed evidence | Mandatory full rollback CI drill through the production `rollback.sh`, real PostgreSQL, backend/frontend/admin/Caddy/Meilisearch and safe external-loop stubs | PASS | Keep the drill mandatory before production isolation |
| Production rollback admission | Named rollback owner -> retained external backup -> production-like host drill -> measured recovery -> signed acceptance | Code-level rollback mechanics and signed evidence validation are complete | PARTIAL | Execute `make rollback-drill` on the pilot host, retain backup/manifest/report and record RTO/RPO |
| Browser E2E | Real browser across Mini App and Admin | Nine stateful Playwright journeys; traces, screenshots, video and HTML evidence on failure | PASS | Keep as required dependency of production Compose isolation |

## Browser journeys

The mandatory browser layer now covers:

1. Telegram bootstrap, discovery, variants, restock, wishlist, discounts, checkout and customer cancellation.
2. Cart quantity increase/decrease and item removal.
3. Profile, loyalty, support ticket, privacy export/request and return registration.
4. Payment return URL polling and paid-order rendering.
5. Admin authentication, promo/product creation, order cancellation and refresh.
6. Pilot runtime status, inventory operations, CSV import/export, initial fulfillment transition and BusinessEvent recovery.
7. Admin support priority/status processing, privacy execution and validated partial refund completion.
8. Admin support-ticket ownership assignment with an accountable active Admin ID.
9. Admin full picklist, packing, readiness, idempotent shipment creation, tracked shipping and delivered/completed terminal state.

## Transactional referral evidence

The mandatory PostgreSQL referral smoke uses the real cart, checkout, payment webhook settlement, loyalty ledger and referral attribution services. It proves that the code is attached before purchase, copied to the first order, rewarded exactly once after `payment.succeeded`, unchanged after duplicate webhooks and a second paid order, and rejected when applied after the first settled purchase.

## Signed backup and restore evidence

Every new PostgreSQL backup must have an adjacent HMAC-SHA256 manifest. The manifest is built from a temporary database restored from the exact compressed archive and binds the archive SHA-256 and size to one Alembic revision, the complete public schema fingerprint and content fingerprints for critical customer, order, payment, refund, inventory, loyalty, referral and pilot-runtime tables.

The mandatory Compose drill proves four fail-closed paths: modified archive bytes are rejected, a mutated live critical row is rejected, the destructive restore returns the original sentinel value, and the restored target exactly matches the signed database snapshot. Production restore commands refuse archives without a valid manifest.

## Full release rollback evidence

The mandatory release rollback drill creates two immutable archives from different git commits and promotes them as previous/current. It starts the current deployment, creates a signed restore-proven backup, mutates a critical customer row and calls the real `scripts/rollback.sh` with evidence recording enabled.

The drill must prove that pilot checkout is stopped, the previous release marker is restored, the database sentinel returns to its backed-up value, transaction and pilot-runtime integrity pass, backend readiness and container smoke pass, Caddy and Meilisearch restart, current/previous pointers swap correctly, both release capabilities remain signed and the rollback evidence validates with different release SHA-256 values. Telegram and long-running worker commands are replaced only inside the CI Compose override so the test cannot call external providers.

## Evidence boundary

The Playwright layer runs the real Mini App and Admin interfaces and uses deterministic stateful API fixtures. It proves browser routing, controls, validation, mutations and UI/API contracts without requiring third-party secrets.

The backup and full release rollback drills use real PostgreSQL, immutable release archives, destructive database recreation and service restart. They do **not** prove external backup retention, production-host permissions, object-storage durability, recovery-time objectives, DNS/HTTPS availability during rollback or a named human operator. Those require one signed production-like admission drill.

It does **not** replace deployed provider evidence. Telegram signatures, YooKassa redirects/webhooks/refunds, MoySklad synchronization, R2/CDN delivery, public DNS/HTTPS and external alert delivery remain live admission requirements.

## Admission rule

A process marked `PARTIAL`, `BLOCKED`, or `NOT COVERED` cannot be represented as fully proven. Code-level evidence may support readiness, but live provider, browser, DNS, HTTPS, legal and operator evidence must be attached before pilot `GO`.
