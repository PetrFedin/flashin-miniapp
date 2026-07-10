# Developer Handover

## Project purpose

FLASHIN Telegram Mini App for selecting and buying clothes inside Telegram.

## Main folders

```text
backend/  FastAPI, SQLAlchemy, Alembic
frontend/ Telegram Mini App
admin/    Admin panel
bot/      Telegram bot
scripts/  Launch, workers, backup, release ops
docs/     Runbooks and checklists
deploy/   Caddy, monitoring, k8s, status page
```

## First local run

```bash
cp .env.local.example .env
make init
```

## Production deploy

```bash
make deploy-prod
```

## Rollback

```bash
make rollback RELEASE=previous.zip BACKUP=backups/flashin_xxx.sql.gz
```

## Key services

- PostgreSQL
- YooKassa
- MoySklad
- Meilisearch
- R2/S3
- Telegram Bot API

## Critical flows

1. Telegram auth.
2. Product catalog.
3. Cart.
4. Checkout.
5. YooKassa payment.
6. Payment webhook.
7. Inventory writeoff.
8. Fulfillment task.
9. Notification.
10. Refund.

## Never deploy without

```bash
python scripts/preflight.py
python scripts/validate_env.py
make test
python tests/e2e_smoke.py
```
