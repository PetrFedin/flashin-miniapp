# Integrated internal-stack E2E gate

This gate closes the gap between the existing mocked browser journeys and the transactional backend smoke suite.

## What `integrated-e2e` proves

The CI job starts a real PostgreSQL 16 service, applies the current Alembic head, starts the real FLASHIN FastAPI application, and serves the real Mini App and Admin applications. Playwright then drives one order through the same database across both user surfaces:

1. Valid Telegram WebApp `initData` is signed with the CI bot token and accepted by `/api/auth/telegram`.
2. The Mini App loads the seeded product from the real `/api/products` endpoint.
3. The customer adds an in-stock variant to the real cart and applies the seeded `FLASH10` promo.
4. Checkout goes through `/api/orders/checkout`, including idempotency, inventory reservation and order persistence.
5. `/api/payments` executes the real FLASHIN payment service and persists a Payment row.
6. Only the external YooKassa HTTP boundary is replaced by `scripts/integrated_e2e_app.py`; it returns a deterministic successful payment and local redirect.
7. Real settlement commits inventory, loyalty/timeline/events and creates the fulfillment task.
8. The real Admin UI logs in against the seeded administrator and completes pick, pack, ready, shipment, tracking and delivery transitions.
9. The Mini App reloads the same order from PostgreSQL and verifies delivered state and tracking number.

The integrated test contains no `page.route(...)` API mocks.

## Safety boundary

`integrated-e2e` is **not** live-provider evidence and must never be presented as such. The wrapper refuses to boot unless `APP_ENV` is `test`/`ci` and `INTEGRATED_E2E=true`. It is not referenced by Docker Compose or production entrypoints. CI explicitly disables the controlled live pilot runtime for this synthetic internal-stack test.

Real pilot admission still requires the signed live lifecycle evidence for Telegram, YooKassa, MoySklad, notification delivery and any enabled search/media providers.

## Governance

Production repository governance must require all six GitHub Actions checks, bound to the official GitHub Actions App source:

`backend,frontend,admin,browser-e2e,integrated-e2e,docker`

`docker` also depends on `integrated-e2e`, so release/rollback verification cannot complete when the internal-stack browser path is red.

## Failure evidence

On failure, GitHub Actions uploads `integrated-e2e-evidence` with the Playwright HTML report, traces, screenshots/video when available, and test results. A failed or skipped `integrated-e2e` is a pilot NO-GO for the exact commit.
