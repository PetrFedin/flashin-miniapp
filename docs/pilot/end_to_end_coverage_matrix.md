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
| Referral | Profile/referral code -> cart attribution -> order | Backend referral tests plus browser profile/code/cart path | PARTIAL | Prove persisted attribution on deployed order |
| Checkout | Cart -> delivery form -> order creation | Transactional journey, idempotency tests and browser checkout/order creation | PASS | Repeat through Telegram MainButton in deployed Mini App |
| Payment creation | Order -> YooKassa payment creation -> redirect | Payment service/review tests plus browser failure-safe order fallback | PARTIAL | Requires YooKassa test credentials and successful redirect evidence |
| Payment callback | Provider webhook -> idempotent domain effect -> paid/review state | Payment idempotency, reconciliation and circuit-breaker tests | PASS | Prove one real duplicate test webhook in pilot environment |
| Payment return | Provider return URL -> order polling -> orders view | Frontend rules, backend status tests and Playwright paid return-route polling | PARTIAL | Prove deployed YooKassa return URL with real sandbox payment |
| Order history | Profile/orders -> order details/status | Backend API tests plus browser profile/orders navigation and refreshed state | PASS | Repeat against deployed customer history |
| Order cancellation | Eligible order -> cancel -> stock/promo/loyalty reversal | Transactional cancellation smoke plus customer and Admin browser cancellation | PASS | Verify deployed stock and loyalty reversal evidence |
| Returns | Eligible paid order -> return request -> review | Backend/refund reconciliation tests plus customer browser return registration | PASS | Add deployed Admin refund-review evidence |
| Partial refunds | Review -> provider refund -> cumulative totals | Cumulative refund smoke and refund integrity tests | PASS | Prove real provider sandbox refund |
| Support | Profile -> create ticket -> read updated ticket list | Backend support tests plus stateful customer browser create/read round trip | PARTIAL | Add Admin processing/status transition and deployed notification evidence |
| Privacy | Profile -> export -> consent/delete request -> tracked state | Backend privacy tests plus browser download and consent-request round trip | PARTIAL | Add deployed retention/legal review and Admin processing evidence |
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
| Admin order operations | Cancellation and paid -> assembling fulfillment transition | Backend state tests plus customer/Admin cancellation and Admin fulfillment browser paths | PARTIAL | Add ready/shipped/completed and refund-review browser transitions |
| Admin pilot operations | Protected status -> GO/NO-GO -> integrity/money attention | Observability tests, metrics/Grafana and browser-valid GO contract | PASS | Verify deployed dashboard, access control and external alerts |
| Runtime pilot guard | Allowlist -> first 20 orders -> automatic STOP | Pilot runtime and circuit-breaker tests | PASS | Requires signed admission and controlled live run |
| Monitoring | Metrics -> Prometheus rules -> Grafana dashboard | Monitoring config/capability tests and production Compose gate | PASS | External receiver and named on-call owner still required |
| Backup/rollback | Backup -> restore -> previous signed release | Release capability and guard tests | PARTIAL | Execute production-like restore/rollback drill |
| Browser E2E | Real browser across Mini App and Admin | Six stateful Playwright journeys; traces, screenshots, video and HTML evidence on failure | PASS | Keep as required dependency of production Compose isolation |

## Browser journeys

The mandatory browser layer now covers:

1. Telegram bootstrap, discovery, variants, restock, wishlist, discounts, checkout and customer cancellation.
2. Cart quantity increase/decrease and item removal.
3. Profile, loyalty, support ticket, privacy export/request and return registration.
4. Payment return URL polling and paid-order rendering.
5. Admin authentication, promo/product creation, order cancellation and refresh.
6. Pilot runtime status, inventory operations, CSV import/export, fulfillment transition and BusinessEvent recovery.

## Evidence boundary

The Playwright layer runs the real Mini App and Admin interfaces and uses deterministic stateful API fixtures. It proves browser routing, controls, validation, mutations and UI/API contracts without requiring third-party secrets.

It does **not** replace deployed provider evidence. Telegram signatures, YooKassa redirects/webhooks/refunds, MoySklad synchronization, R2/CDN delivery, public DNS/HTTPS and external alert delivery remain live admission requirements.

## Admission rule

A process marked `PARTIAL`, `BLOCKED`, or `NOT COVERED` cannot be represented as fully proven. Code-level evidence may support readiness, but live provider, browser, DNS, HTTPS, legal and operator evidence must be attached before pilot `GO`.
