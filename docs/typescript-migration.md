# FLASHIN TypeScript migration contract

## Binding architecture decision

The production target is Node.js 22 and TypeScript. Python, FastAPI, aiogram, SQLAlchemy and Alembic are legacy implementation details and are frozen. Existing Python files may be deleted during migration, but new or modified Python runtime files are rejected by CI.

The target integrations are:

- Telegram Bot API and Telegram Mini App;
- T-Bank internet acquiring for payment, cancellation and refund flows;
- Tilda catalog exports in CSV or YML as the catalog source;
- PostgreSQL for durable business state;
- Redis-backed queues only where asynchronous delivery is required.

## Migration order

1. Commerce domain invariants: money, pricing, checkout idempotency, inventory reservations and state transitions.
2. T-Bank request signing, notification verification, payment attempts, reconciliation and refunds.
3. Tilda catalog ingestion, product identity, variants, stock synchronization and import audit.
4. TypeScript API and PostgreSQL repositories with transaction and locking tests.
5. Telegram bot state machine and Mini App authentication.
6. Admin, fulfillment, delivery, notifications, support, analytics and operational jobs.
7. Remove the legacy Python containers, dependencies, migrations and CI jobs.

## Rules

- Money is stored and transported in integer minor units; binary floating point is not permitted for financial calculations.
- Every externally retried write requires an idempotency key and a deterministic request fingerprint.
- Inventory has one source of truth and reservations are explicit, auditable and terminal.
- Order, payment and delivery states are changed only through owned workflows.
- Payment notifications are verified before any business mutation and duplicate events are harmless.
- Catalog identities prefer TildaUID, then External ID, then a deterministic SKU and variant identity.
- Demo data, mock providers and bypass flags are not allowed in production paths.

## First implemented slice

`platform/` contains the first executable TypeScript slice with focused regression tests for the highest-risk invariants. It is intentionally independent from the legacy Python runtime so it can become the shared domain layer for the API, bot, workers and administrative UI.
