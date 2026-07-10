# FLASHIN v41 — platform maturity layer

## Added

### Installer

```bash
./scripts/install.sh
```

Checks Docker, creates `.env`, runs preflight, builds containers, migrates DB and starts the app.

### CI/CD

```text
.github/workflows/ci.yml
.github/workflows/release.yml
```

CI covers:

- backend compile;
- pytest;
- frontend build;
- admin build;
- docker build.

### Feature Flags and Remote Config

Tables:

```text
feature_flags
remote_configs
```

Endpoints:

```text
GET  /api/platform/features
POST /api/platform/admin/features
GET  /api/platform/remote-config
POST /api/platform/admin/remote-config
```

### CMS

Tables:

```text
cms_pages
cms_blocks
```

Endpoints:

```text
GET  /api/platform/cms/pages/{slug}
GET  /api/platform/cms/blocks/{page_slug}
POST /api/platform/admin/cms/pages
POST /api/platform/admin/cms/blocks
```

### Business Event Dispatcher

Table:

```text
business_events
```

Service:

```text
backend/services/event_dispatcher.py
```

Job:

```bash
python scripts/run_event_jobs.py
```

### Audit Trail v2

Table:

```text
audit_trails
```

Stores before/after JSON, IP, user-agent.

### Media Pipeline

Table:

```text
media_derivatives
```

Generates local:

- thumbnail webp;
- full webp.

### Recommendation Engine v2

Service:

```text
backend/services/recommendation_engine.py
```

Adds category + brand based scoring and personal recommendations from wishlist/category history.

### Scheduler

```text
backend/jobs/scheduler_app.py
scripts/run_scheduler.py
```

Runs:

- due campaigns;
- event dispatcher;
- abandoned carts;
- inventory snapshots;
- SLA overdue checks;
- outbox processing.

### Import/Export Framework

Endpoints:

```text
POST /api/import-export/admin/export/products
POST /api/import-export/admin/export/orders
```

### Kubernetes Starter

```text
deploy/k8s/
```

Includes namespace, backend deployment/service, frontend deployment/service and ingress.

## Why this matters

v40 simplified launch. v41 adds long-term platform mechanics:

```text
feature toggles
remote configuration
content management
event bus
media processing
stronger audit trail
scheduler
CI/CD
K8s starter manifests
```
