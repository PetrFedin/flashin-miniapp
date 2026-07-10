# First-run Error Map

## Docker is not running

Symptom:

```text
Cannot connect to the Docker daemon
```

Fix:

```bash
open Docker Desktop
docker compose version
```

## Database is not ready

Symptom:

```text
connection refused db:5432
```

Fix:

```bash
docker compose up -d db
docker compose logs -f db
```

## Alembic migration failed

Symptom:

```text
alembic.util.exc.CommandError
```

Fix:

```bash
docker compose run --rm backend alembic -c backend/alembic.ini current
docker compose run --rm backend alembic -c backend/alembic.ini upgrade head
```

## Telegram auth fails

Check:

```text
TELEGRAM_BOT_TOKEN
BotFather domain
Mini App opened inside Telegram, not browser
```

## YooKassa payment fails

Check:

```text
YOOKASSA_SHOP_ID
YOOKASSA_SECRET_KEY
YOOKASSA_RETURN_URL
Webhook URL in YooKassa dashboard
```

## MoySklad sync fails

Check:

```text
MOYSKLAD_TOKEN
MOYSKLAD_LOGIN / MOYSKLAD_PASSWORD
MoySklad API access
Mapping rules
```

## Images do not load

Check:

```text
MEDIA_STORAGE
MEDIA_PUBLIC_BASE_URL
R2/S3 credentials
frontend/public/fallback-product.svg
```

## Admin cannot login

Check:

```text
ADMIN_EMAIL
ADMIN_PASSWORD
scripts/seed_admin.py
admin user active
```

## Workers do not run

Check:

```bash
docker compose --profile workers up -d
docker compose logs -f media_jobs
docker compose logs -f sla_jobs
```
