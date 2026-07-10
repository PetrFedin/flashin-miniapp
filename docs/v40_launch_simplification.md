# FLASHIN v40 — launch simplification layer

## Fixed

### Dockerfile.backend

Now copies:

```text
backend/
scripts/
```

Worker commands can now access:

```text
scripts/run_ops_jobs.py
scripts/run_outbox_jobs.py
scripts/run_moysklad_sync.py
scripts/run_campaign_jobs.py
scripts/run_sla_jobs.py
```

### Dockerfile.bot

Now copies:

```text
bot/
backend/
```

So `bot/send_notifications.py` can import backend models.

### Frontend/Admin Dockerfiles

Now use production build:

```text
npm run build
Caddy file-server
```

No more `npm run dev` for packaged Docker start.

### Environments

Added:

```text
.env.local.example
.env.production.example
```

### Launch scripts

Added:

```text
scripts/bootstrap.sh
scripts/migrate.sh
scripts/healthcheck.sh
```

### Makefile

Added common commands:

```text
make init
make migrate
make health
make workers
make search
make monitoring
make test
make backup
```

### Docker healthchecks

Added for:

```text
db
backend
```

### E2E smoke

Added:

```text
tests/e2e_smoke.py
```

## Remaining manual steps before public launch

1. Fill production secrets.
2. Configure BotFather domain.
3. Configure YooKassa webhook.
4. Configure MoySklad token/mapping rules.
5. Replace legal pages.
6. Run 20-order pilot.
7. Enable backups and cron workers.
