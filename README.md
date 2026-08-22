# FLASHIN Telegram Mini App

Production-oriented Telegram Mini App for choosing and buying FLASHIN clothes.

## What is inside

```text
frontend/        customer Telegram Mini App
admin/           admin panel
backend/         FastAPI backend
bot/             Telegram bot
PostgreSQL       main database
YooKassa         payments/refunds
MoySklad         catalog/stock source
Meilisearch      optional search engine
R2/S3            media storage
Caddy            production reverse proxy/HTTPS
Prometheus       metrics
Grafana          dashboards
Alembic          production migrations
scripts/         launch, migrate, backup, jobs, checks
docs/            runbooks and QA
```

## Fast local start

```bash
cp .env.local.example .env
# fill TELEGRAM_BOT_TOKEN and JWT_SECRET
make init
```

Open:

```text
Mini App: http://localhost:5173
Admin:    http://localhost:5174
API:      http://localhost:8000
```

## Main commands

```bash
make help
make build
make up
make migrate
make health
make workers
make search
make monitoring
make test
make backup
```

## Production and pilot start

```bash
cp .env.production.example .env
# fill every production secret, integration credential and public URL

make readiness-gate  # must return GO before deploy
make deploy-prod      # backup, migrations, services and internal smoke
make pilot-gate       # must return GO before admitting pilot users
```

`make launch-production` delegates to the same hardened production deploy and cannot bypass the production Compose overlay.

The strict gates intentionally return **NO-GO** while legal pages still contain template wording, seller details are missing, production secrets are unsafe, Compose isolation is broken, migrations are stale or public endpoints are unavailable.

Pilot procedure and stop criteria: `docs/pilot/pilot_launch_runbook.md`.

## Required production values

```env
TELEGRAM_BOT_TOKEN=
JWT_SECRET=
ADMIN_EMAIL=
ADMIN_TOTP_ENCRYPTION_KEY=
YOOKASSA_SHOP_ID=
YOOKASSA_SECRET_KEY=
MOYSKLAD_TOKEN=
MEILISEARCH_MASTER_KEY=
OUTBOX_SIGNING_SECRET=
```

`ADMIN_PASSWORD` is deliberately **not** a production environment value. On a fresh production database, the first owner password is entered twice through the hidden interactive `scripts/seed_admin.py` prompt, then TOTP is enrolled through the offline first-admin MFA bootstrap. See `docs/runbooks/admin_mfa_bootstrap.md`.

## Database

Production uses Alembic:

```bash
make migrate
```

Do not use `USE_CREATE_ALL=true` in production.

## Workers

```bash
make workers
```

The production deployment starts the notification worker and distributed scheduler. Scheduler jobs use PostgreSQL advisory locks to prevent the same job from running concurrently in different containers.

## Search

```bash
make search
```

This starts Meilisearch and applies product index settings.

## Monitoring

```bash
make monitoring
```

Prometheus:

```text
http://localhost:9090
```

Grafana:

```text
http://localhost:3000
```

## Legal

These files must contain final approved seller details and text before pilot launch:

```text
frontend/public/legal/offer.html
frontend/public/legal/privacy.html
frontend/public/legal/returns.html
```

The readiness gate detects known placeholder phrases but does not replace legal approval.

## QA

```bash
python tests/e2e_smoke.py
pytest backend/tests
```

CI also runs transactional customer journey, cancellation, payment review, cumulative refund, business event, webhook lease, notification lease, scheduler lock and refund reconciliation smoke scenarios against PostgreSQL.

## Important

The current launch path is fail-closed:

- production launch cannot use the development Compose graph;
- only Caddy publishes host ports in production;
- backend health is based on `/ready`, including database and Alembic state;
- migrations are checked before application admission;
- existing databases are audited and backed up before migration;
- strict predeploy and live pilot reports are written to `docs/`;
- pilot users are not admitted until both gates return GO.


## v41 — platform maturity layer

Добавлены installer, CI/CD workflows, feature flags, remote config, CMS, business event dispatcher, audit trail v2, media pipeline, recommendation engine v2, scheduler, import/export framework, K8s starter manifests and disaster recovery docs. См. `docs/v41_platform_layer.md`, `docs/v41_install_and_release.md`, `docs/disaster_recovery.md`.


## v42 — release operations layer

Добавлены production deploy script, rollback, backup verification, seed admin, API versioning scaffold, secrets manager template, release manifest and release checklist. См. `docs/v42_release_operations.md` и `docs/v42_release_checklist.md`.


## v43 — operations quality layer

Добавлены self-diagnostics, env validator, OpenAPI snapshot generator, release notes generator, status page scaffold, developer handover and runbook index. См. `docs/v43_operations_quality_layer.md`, `docs/developer_handover.md`, `docs/runbook_index.md`.


## v44 — launch cockpit layer

Добавлены setup wizard, unified integration check, production readiness report, 20-order pilot sheet generator, expanded legal templates and launch cockpit docs. См. `docs/v44_launch_cockpit.md` и `docs/v44_what_to_fill_before_launch.md`.


## v45 — final launch discipline layer

Добавлены readiness gate, launch command center, incident templates, support/admin SOP, data retention policy, post-launch metrics plan, master launch checklist and final acceptance criteria. См. `docs/v45_launch_command_center.md`, `docs/v45_master_launch_checklist.md` и `docs/v45_final_acceptance.md`.


## v46 — post-launch scale layer

Добавлены post-launch review pack, KPI dashboard spec, bug/feature/feedback templates, support handover pack, roadmap backlog, performance budget checker and k6 load-test scaffold. См. `docs/post_launch/` и `deploy/loadtest/`.


## v47 — security e2e delivery observability hardening

Добавлены real E2E scaffold, admin security foundation, payment reconciliation, delivery providers/shipments, Grafana dashboard scaffold, MoySklad deep mapping, media processing jobs, security audit script and extended k6 load tests. См. `docs/v47_security_e2e_delivery_observability.md` и `docs/v47_e2e_scenarios.md`.


## v48 — deep hardening layer

Углублены реальные E2E runner, admin security APIs, CSP/security headers, delivery quotes, Grafana dashboards, MoySklad deep mapping v2, CDN purge hook, fallback image, admin UX endpoint map. См. `docs/v48_deep_hardening.md`.


## v49 — simple connected launch layer

Добавлены единый `scripts/launch.py`, `start_simple.sh`, connected system audit, simplicity score, unified system map, final gap analysis and 30-minute launch plan. См. `docs/v49_unified_system_map.md`, `docs/v49_final_gap_analysis.md`, `docs/v49_30_minute_launch_plan.md`.


## v50 — final handover and live pilot layer

Добавлены env todo generator, live pilot runner, release pack generator, first-run error map, operator/developer handover docs and final handover. См. `docs/v50_final_handover.md`, `docs/handover/`, `docs/pilot/`.


## v51 — pilot freeze layer

Добавлены release freeze, pilot evidence log, acceptance sign-off, order/payment/stock reconciliation sheet and what-not-to-touch before pilot. См. `docs/v51_pilot_freeze_layer.md` и `docs/acceptance/`.


## v52 — final audit and launch sanity layer

Добавлены финальный package audit, test runner, тестовые env defaults для pytest, обновлена версия backend title, `USE_CREATE_ALL` по умолчанию выключен. См. `docs/audit/v52_final_audit_summary.md`.


## v53 — executable pilot readiness layer

Readiness переведён из проверки наличия файлов в исполняемый fail-closed процесс. Добавлены строгие predeploy/live gates, контроль финальности юридических страниц, migration-aware `/ready`, безопасный production launcher, расширенный Compose gate и единый pilot launch runbook.
