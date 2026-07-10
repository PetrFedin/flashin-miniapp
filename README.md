# FLASHIN Telegram Mini App v40

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

## Production start

```bash
cp .env.production.example .env
# fill all production secrets
make preflight
make build
make migrate
docker compose --profile production up -d
```

## Required production values

```env
TELEGRAM_BOT_TOKEN=
JWT_SECRET=
ADMIN_EMAIL=
ADMIN_PASSWORD=
YOOKASSA_SHOP_ID=
YOOKASSA_SECRET_KEY=
MOYSKLAD_TOKEN=
MEILISEARCH_MASTER_KEY=
OUTBOX_SIGNING_SECRET=
```

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

Workers:

```text
notification_worker
ops_jobs
outbox_jobs
moysklad_sync
campaign_jobs
sla_jobs
```

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

Replace these files with final lawyer-approved text before launch:

```text
frontend/public/legal/offer.html
frontend/public/legal/privacy.html
frontend/public/legal/returns.html
```

## QA

```bash
python tests/e2e_smoke.py
pytest backend/tests
```

## Important

v40 is focused on launch simplicity:

- worker containers can find scripts;
- bot container can import backend models;
- frontend/admin use production build;
- env files are separated;
- bootstrap/migrate/health commands exist;
- docker healthchecks are configured;
- README is now the main launch document.


## v41 — platform maturity layer

Добавлены installer, CI/CD workflows, feature flags, remote config, CMS, business event dispatcher, audit trail v2, media pipeline, recommendation engine v2, scheduler, import/export framework, K8s starter manifests and disaster recovery docs. См. `docs/v41_platform_layer.md`, `docs/v41_install_and_release.md`, `docs/disaster_recovery.md`.


## v42 — release operations layer

Добавлены production deploy script, rollback, backup verification, seed admin, API versioning scaffold, secrets manager template, release manifest and release checklist. См. `docs/v42_release_operations.md` и `docs/v42_release_checklist.md`.


## v43 — operations quality layer

Добавлены self-diagnostics, env validator, OpenAPI snapshot generator, release notes generator, status page scaffold, developer handover and runbook index. См. `docs/v43_operations_quality_layer.md`, `docs/developer_handover.md`, `docs/runbook_index.md`.


## v44 — launch cockpit layer

Добавлены setup wizard, unified integration check, production readiness report, 20-order pilot sheet generator, expanded legal templates and launch cockpit docs. См. `docs/v44_launch_cockpit.md` и `docs/v44_what_to_fill_before_launch.md`.


## v45 — final launch discipline layer

Добавлены readiness gate, launch command center, incident templates, support/admin SOP, data retention policy, post-launch metrics plan, master launch checklist and final acceptance criteria. См. `docs/v45_launch_command_center.md`, `docs/v45_master_launch_checklist.md`, `docs/v45_final_acceptance.md`.


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
