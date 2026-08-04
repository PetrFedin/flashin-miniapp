# FLASHIN pilot end-to-end coverage matrix

Status values: `PASS`, `PARTIAL`, `BLOCKED`, `NOT COVERED`.

| Process | Customer / operator path | Current automated evidence | Status | Pilot action |
|---|---|---|---|---|
| Telegram authentication | Telegram init data -> backend auth -> customer session | Backend auth/trust-domain tests plus Playwright Telegram WebApp bootstrap | PARTIAL | Prove one deployed Mini App session with real signed Telegram init data |
| Storefront bootstrap | Auth -> catalog -> cart -> looks -> wishlist | Frontend loader tests, production build and Playwright visible catalog bootstrap | PASS | Repeat against deployed pilot API and Telegram domain |
| Product discovery | Catalog -> search -> product card -> product details | API/frontend tests plus Playwright catalog-to-product navigation | PASS | Verify deployed search with production Meilisearch index |
| Product variants | Product -> size/variant -> stock availability -> restock subscription | Backend variant/stock tests plus browser in-stock size and size-helper path | PARTIAL | Add browser out-of-stock/restock assertion and live stock sync evidence |
| Wishlist | Product -> add/remove wishlist -> profile wishlist | Backend endpoints plus Playwright add-to-wishlist action | PARTIAL | Add profile persistence/removal browser round trip |
| Cart | Product -> cart -> quantity update/remove | Transactional customer journey smoke plus browser add/open-cart path | PASS | Extend browser assertions to quantity and removal controls |
| Promotions | Cart -> promo validation -> recalculated totals | Backend constraints/checkout tests plus Playwright successful promo path | PASS | Add deployed invalid/expired promo evidence |
| Loyalty | Cart -> reserve points -> order -> cancellation/refund reversal | Transactional smokes plus Playwright points reservation path | PASS | Verify deployed balance after cancellation/refund |
| Referral | Profile/referral code -> cart attribution -> order | Backend referral tests plus Playwright referral entry path | PARTIAL | Prove persisted attribution on deployed order |
| Checkout | Cart -> delivery form -> order creation | Transactional journey, idempotency tests and Playwright checkout form/order creation | PASS | Repeat through Telegram MainButton in deployed Mini App |
| Payment creation | Order -> YooKassa payment creation -> redirect | Payment service/review tests plus browser failure-safe order fallback | PARTIAL | Requires YooKassa test credentials and successful redirect evidence |
| Payment callback | Provider webhook -> idempotent domain effect -> paid/review state | Payment idempotency, reconciliation and circuit-breaker tests | PASS | Prove one real duplicate test webhook in pilot environment |
| Payment return | Provider return URL -> order polling -> orders view | Frontend payment-return rules and backend status tests | PARTIAL | Add Playwright callback-route test and deployed provider return evidence |
| Order history | Profile/orders -> order details/status | Backend API/frontend loader tests plus browser transition to orders after checkout | PARTIAL | Add direct browser order-history navigation and status refresh assertions |
| Order cancellation | Eligible order -> cancel -> stock/promo/loyalty reversal | Transactional cancellation smoke plus Admin Playwright cancellation | PASS | Add customer browser cancellation evidence |
| Returns | Eligible order -> return request -> review | Backend return tests and refund reconciliation review smoke | PASS | Add customer and admin browser return round trip |
| Partial refunds | Review -> provider refund -> cumulative totals | Cumulative refund smoke and refund integrity tests | PASS | Prove real provider sandbox refund |
| Support | Profile -> create ticket -> admin processing | Backend support tests and frontend profile loader | PARTIAL | Add customer/admin browser round trip |
| Privacy | Profile -> export/delete request -> admin processing | Backend privacy tests and frontend loader | PARTIAL | Add browser download/request evidence |
| Notifications | Domain event -> notification outbox -> lease -> send/retry | Notification lease smoke and retry-state tests | PASS | Prove Telegram sandbox delivery |
| Business events | Commit -> durable event -> worker -> outboxes | Worker and recovery smoke tests | PASS | Add browser operator recovery path and monitor poison-event alert |
| Webhooks | Event -> destination outbox -> leased delivery/retry | Webhook lease smoke and integrity tests | PASS | Add external receiver sandbox evidence |
| Scheduler | Scheduled job -> distributed lock -> one execution | Scheduler lock smoke | PASS | Observe scheduled execution in deployed pilot |
| Stock sync | MoySklad -> mapping -> local product/variant/stock | Backend sync tests | PARTIAL | Requires token and 5-10 real products/variants |
| Search index | Product changes -> Meilisearch indexing -> storefront search | Backend search tests and production graph | PARTIAL | Requires production key and index rebuild evidence |
| Media | Upload -> object storage -> public delivery -> purge | Backend media tests and production graph | PARTIAL | Requires R2/S3/CDN credentials and live evidence |
| Admin authentication | Login -> trusted admin session -> protected sections | Admin security tests/build plus Playwright login/logout | PASS | Add browser session-expiry assertion against deployed admin |
| Admin product/promo operations | Login -> promo create -> product create -> refreshed lists | Admin unit/build checks plus stateful Playwright mutations | PASS | Repeat with deployed pilot database and audit evidence |
| Admin order operations | Order list -> status/payment/return actions | Backend state tests plus Playwright eligible-order cancellation | PARTIAL | Extend browser coverage to fulfillment and refund review transitions |
| Admin pilot operations | Protected status -> runtime/integrity/money attention | Observability tests, metrics and Grafana provisioning | PASS | Verify deployed dashboard and external alerts |
| Runtime pilot guard | Allowlist -> first 20 orders -> automatic STOP | Pilot runtime and circuit-breaker tests | PASS | Requires signed admission and controlled live run |
| Monitoring | Metrics -> Prometheus rules -> Grafana dashboard | Monitoring config/capability tests and production Compose gate | PASS | External receiver and named on-call owner still required |
| Backup/rollback | Backup -> restore -> previous signed release | Release capability and guard tests | PARTIAL | Execute production-like restore/rollback drill |
| Browser E2E | Real browser across Mini App and Admin | Mandatory Playwright CI gate with traces, screenshots, video and HTML report on failure | PASS | Keep as required dependency of production Compose isolation |

## Evidence boundary

The Playwright layer runs the real Mini App and Admin interfaces and uses deterministic stateful API fixtures. It proves browser routing, controls, validation, mutations and UI/API contracts without requiring third-party secrets.

It does **not** replace deployed provider evidence. Telegram signatures, YooKassa redirects/webhooks/refunds, MoySklad synchronization, R2/CDN delivery, public DNS/HTTPS and external alert delivery remain live admission requirements.

## Admission rule

A process marked `PARTIAL`, `BLOCKED`, or `NOT COVERED` cannot be represented as fully proven. Code-level evidence may support readiness, but live provider, browser, DNS, HTTPS, legal and operator evidence must be attached before pilot `GO`.
