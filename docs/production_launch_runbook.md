# Production launch runbook

This runbook describes the current production launch sequence. The authoritative
runtime contract is `.env.production.example` plus the production validators in
`backend/config.py` and `scripts/check_production_compose.py`.

Real money and outbound commerce providers are **not** part of the first public
production profile.

## 1. Promote one exact release

Before deployment:

1. `main` must contain the intended release commit.
2. CI and Security must be green for the exact release evidence used for launch.
3. Current and previous release archives must verify successfully.
4. Signed backup/restore and rollback evidence must be current.
5. Do not promote a release while repository-governance evidence is stale or
   unresolved.

Never substitute an older green SHA for the release being deployed.

## 2. First public production profile: provider-disabled

Start with the customer/admin product surface live while commercial checkout,
payment execution and MoySklad execution remain disabled.

Required authority:

```env
APP_ENV=production

COMMERCIAL_CHECKOUT_ENABLED=false
PAYMENTS_MODE=disabled
MOYSKLAD_MODE=disabled
MOYSKLAD_ORDER_EXPORT_ENABLED=false
PILOT_RUNTIME_ENFORCED=false

RATE_LIMIT_ENABLED=true
RATE_LIMIT_BACKEND=redis

USE_CREATE_ALL=false
ENABLE_SEED=false
```

These values are enforced server-side. They are not frontend feature flags:

- `POST /api/orders/checkout` fails closed while commercial checkout is disabled.
- `POST /api/payments` fails closed while payment execution is disabled.
- MoySklad execution paths fail closed or remain disabled while
  `MOYSKLAD_MODE=disabled`.
- `GET /api/platform/capabilities` is the public runtime projection that the
  Mini App and Admin should use to render capability state.

Do not configure fake provider credentials and do not emulate provider success.

## 3. Production infrastructure and secrets

Expected public surfaces:

- Mini App: `https://mini.flashin.store`
- API: `https://api.flashin.store`
- Admin: `https://admin.flashin.store`

Minimum production dependencies:

- PostgreSQL;
- shared Redis rate-limit authority;
- Telegram bot credentials;
- unique JWT, Admin TOTP encryption and outbox signing secrets;
- HTTPS and explicit CORS origins;
- monitoring and an external alert receiver.

The first Admin password is **not** stored in production environment variables.
Create the owner only through the hidden interactive `scripts/seed_admin.py`
bootstrap and enroll TOTP through the documented MFA flow.

R2/S3 and Meilisearch are independent infrastructure capabilities:

- if `MEDIA_STORAGE=r2` or `s3`, configure real storage credentials;
- if durable object storage is not ready, use only a deployment topology that
  provides a durable writable media volume and explicitly select `local`;
- if `MEILISEARCH_ENABLED=true`, configure a strong master key and working
  service;
- otherwise set `MEILISEARCH_ENABLED=false` and use database search.

## 4. Database and runtime startup

For the exact deployed release:

1. validate the production environment;
2. require one Alembic head;
3. run `alembic -c backend/alembic.ini upgrade head`;
4. start Redis and PostgreSQL before application workers;
5. start API, Admin, Mini App and bot;
6. start only workers whose configured capabilities are enabled;
7. verify `/health`, `/ready` and `/api/platform/capabilities`.

The resolved production Compose graph must pass
`python scripts/check_production_compose.py`.

## 5. Provider-disabled live smoke

The first public smoke proves the platform without authorizing money movement:

- Telegram opens the real Mini App;
- authentication succeeds with real Telegram signed data;
- catalog and product pages load;
- cart operations persist;
- Admin authentication, RBAC and core read paths work;
- search and media work in their configured modes;
- `/api/platform/capabilities` reports:
  - commercial checkout disabled;
  - payments disabled;
  - MoySklad disabled;
  - controlled commerce pilot disabled;
- attempts to call disabled commercial endpoints fail closed with the documented
  503 capability errors;
- logs contain no YooKassa or MoySklad outbound execution caused by customer
  traffic.

A provider-disabled production launch is **not** approval for real sales.

## 6. Controlled provider activation

Activate providers as separate, auditable changes. Do not switch all providers
on at once.

### 6.1 Payment sandbox / controlled commerce

Before payment execution:

- complete external credentials and business/legal sign-off;
- create fresh provider and live-readiness evidence;
- complete repository-governance evidence for the exact release;
- verify public HTTPS, Telegram lifecycle, monitoring and rollback;
- arm the explicit allowlist and controlled 20-order pilot.

Only then change the money boundary together:

```env
COMMERCIAL_CHECKOUT_ENABLED=true
PAYMENTS_MODE=sandbox
PILOT_RUNTIME_ENFORCED=true
PILOT_RUNTIME_MAX_ORDERS=20
```

Use real sandbox credentials and prove redirect, callback idempotency, refund and
reconciliation before moving `PAYMENTS_MODE` to `live`.

### 6.2 MoySklad

Enable MoySklad independently after payment/checkout state is stable.

Required before live outbound execution:

- valid MoySklad credentials;
- authoritative sellable store ID;
- distinct damaged and quarantine store IDs;
- organization/agent/delivery-service IDs required by the enabled workflows;
- inbound sellable-stock proof;
- outbound order/return/disposition proof;
- ambiguity/retry evidence.

Then enable `MOYSKLAD_MODE` deliberately. Keep
`MOYSKLAD_ORDER_EXPORT_ENABLED=false` until outbound document execution has its
own GO evidence.

## 7. Real-money pilot and mass launch

The controlled pilot is capped at 20 orders and remains subject to the signed
admission/circuit-breaker contract. Any unresolved payment, refund, stock,
fulfillment, notification or reconciliation incident is a STOP/review condition.

Mass launch is a separate decision after all pilot orders are reconciled and the
external launch gates in `docs/missing_before_real_sales.md` are closed.
