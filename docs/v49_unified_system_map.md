# FLASHIN v49 — Unified System Map

## Main commercial chain

```text
Telegram Channel
→ Telegram Bot
→ Mini App
→ Catalog
→ Product Card
→ Cart
→ Checkout
→ YooKassa Payment
→ Payment Webhook
→ Paid Order
→ Inventory Writeoff
→ Fulfillment Task
→ Delivery Shipment
→ Customer Notification
→ Support / Refund if needed
```

## Backend modules

```text
auth
products
cart
orders
payments
returns
delivery
fulfillment
moysklad
media
crm
loyalty
referral
support
privacy
search
recommendations
cms
platform
analytics
diagnostics
admin security
observability
```

## Data sources

```text
PostgreSQL — main state
MoySklad — product/stock source
YooKassa — payment source
Telegram — user entry/auth/channel
R2/S3 — production media storage
Meilisearch — search index
Prometheus/Grafana — monitoring
```

## Operational scripts

```text
scripts/launch.py
scripts/start_simple.sh
scripts/preflight.py
scripts/validate_env.py
scripts/check_integrations.py
scripts/readiness_gate.py
scripts/deploy_production.sh
scripts/rollback.sh
scripts/backup_postgres.sh
scripts/restore_postgres.sh
scripts/connected_system_audit.py
scripts/simplicity_score.py
```

## What is now simplified

For local/pilot launch:

```bash
python3 scripts/launch.py --mode local --with-search --with-workers
```

Or:

```bash
./scripts/start_simple.sh
```

For production:

```bash
python3 scripts/launch.py --mode production --with-search --with-workers --with-monitoring
```

## What still requires real credentials

```text
TELEGRAM_BOT_TOKEN
BotFather domain
YOOKASSA_SHOP_ID
YOOKASSA_SECRET_KEY
MOYSKLAD_TOKEN
S3/R2 credentials
Legal final texts
Real FLASHIN product data
```
