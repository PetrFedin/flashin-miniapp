# FLASHIN pilot end-to-end coverage matrix

Status values: `PASS`, `PARTIAL`, `BLOCKED`, `NOT COVERED`.

| Process | Customer / operator path | Current automated evidence | Status | Pilot action |
|---|---|---|---|---|
| Telegram authentication | Telegram init data -> backend auth -> customer session | Backend auth and trust-domain tests; frontend bootstrap tests | PARTIAL | Add real Mini App browser run with signed Telegram test init data |
| Storefront bootstrap | Auth -> catalog -> cart -> looks -> wishlist | Frontend loader tests and production build | PARTIAL | Add browser assertions for visible sections and recoverable partial failures |
| Product discovery | Catalog -> search -> product card -> product details | API tests and frontend rules tests | PARTIAL | Add browser route and interaction coverage |
| Product variants | Product -> size/variant -> stock availability -> restock subscription | Backend variant/stock tests; frontend input tests | PARTIAL | Add browser checks for in-stock and out-of-stock states |
| Wishlist | Product -> add/remove wishlist -> profile wishlist | Backend endpoints and frontend state rules | PARTIAL | Add browser persistence check across reload |
| Cart | Product -> cart -> quantity update/remove | Transactional customer journey smoke and frontend action locks | PASS | Add browser visual/state assertions |
| Promotions | Cart -> promo validation -> recalculated totals | Backend promo constraints and checkout tests | PASS | Add browser error/success states |
| Loyalty | Cart -> reserve points -> order -> cancellation/refund reversal | Customer journey, cancellation and cumulative refund smoke tests | PASS | Add browser balance and reservation assertions |
| Referral | Profile/referral code -> cart attribution -> order | Backend referral tests | PARTIAL | Add browser entry and attribution evidence |
| Checkout | Cart -> delivery form -> order creation | Transactional customer journey smoke; checkout idempotency tests | PASS | Add browser form and Telegram MainButton path |
| Payment creation | Order -> YooKassa payment creation -> redirect | Payment service tests and payment review smoke | PARTIAL | Requires YooKassa test credentials and browser redirect evidence |
| Payment callback | Provider webhook -> idempotent domain effect -> paid/review state | Payment idempotency, reconciliation and circuit-breaker tests | PASS | Prove one real duplicate test webhook in pilot environment |
| Payment return | Provider return URL -> order polling -> orders view | Frontend payment-return rules and backend status tests | PARTIAL | Add browser callback route test |
| Order history | Profile/orders -> order details/status | Backend API tests and frontend loader tests | PARTIAL | Add browser navigation and state assertions |
| Order cancellation | Eligible order -> cancel -> stock/promo/loyalty reversal | Transactional cancellation smoke | PASS | Add operator/browser evidence |
| Returns | Eligible order -> return request -> review | Backend return tests and refund reconciliation review smoke | PASS | Add customer and admin browser flow |
| Partial refunds | Review -> provider refund -> cumulative totals | Cumulative refund smoke and refund integrity tests | PASS | Prove real provider sandbox refund |
| Support | Profile -> create ticket -> admin processing | Backend support tests and frontend profile loader | PARTIAL | Add customer/admin browser round trip |
| Privacy | Profile -> export/delete request -> admin processing | Backend privacy tests and frontend loader | PARTIAL | Add browser download/request evidence |
| Notifications | Domain event -> notification outbox -> lease -> send/retry | Notification lease smoke and retry-state tests | PASS | Prove Telegram sandbox delivery |
| Business events | Commit -> durable event -> worker -> outboxes | Worker and recovery smoke tests | PASS | Monitor poison-event alert in production-like environment |
| Webhooks | Event -> destination outbox -> leased delivery/retry | Webhook lease smoke and integrity tests | PASS | Add external receiver sandbox evidence |
| Scheduler | Scheduled job -> distributed lock -> one execution | Scheduler lock smoke | PASS | Observe scheduled execution in deployed pilot |
| Stock sync | MoySklad -> mapping -> local product/variant/stock | Backend sync tests | PARTIAL | Requires token and 5-10 real products/variants |
| Search index | Product changes -> Meilisearch indexing -> storefront search | Backend search tests and production graph | PARTIAL | Requires production key and index rebuild evidence |
| Media | Upload -> object storage -> public delivery -> purge | Backend media tests and production graph | PARTIAL | Requires R2/S3/CDN credentials and live evidence |
| Admin authentication | Login -> trusted admin session -> protected sections | Admin session security tests and admin build | PARTIAL | Add browser login/session-expiry checks |
| Admin order operations | Order list -> status/payment/return actions | Backend admin and order-state tests | PARTIAL | Add browser operator workflow |
| Admin pilot operations | Protected status -> runtime/integrity/money attention | Observability tests, metrics and Grafana provisioning | PASS | Verify deployed dashboard and external alerts |
| Runtime pilot guard | Allowlist -> first 20 orders -> automatic STOP | Pilot runtime and circuit-breaker tests | PASS | Requires signed admission and controlled live run |
| Monitoring | Metrics -> Prometheus rules -> Grafana dashboard | Monitoring config/capability tests and production Compose gate | PASS | External receiver and named on-call owner still required |
| Backup/rollback | Backup -> restore -> previous signed release | Release capability and guard tests | PARTIAL | Execute production-like restore/rollback drill |
| Browser E2E | Real browser across Mini App and Admin | No Playwright/Cypress browser suite currently present | NOT COVERED | Add Playwright pilot gate before live admission |

## Admission rule

A process marked `PARTIAL`, `BLOCKED`, or `NOT COVERED` cannot be represented as fully proven. Code-level evidence may support readiness, but live provider, browser, DNS, HTTPS, legal and operator evidence must be attached before pilot `GO`.
