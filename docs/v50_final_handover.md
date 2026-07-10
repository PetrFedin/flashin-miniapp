# FLASHIN v50 — Final Handover

## What this package is

This is a complete Telegram commerce project package for FLASHIN:

```text
Telegram Mini App
Admin panel
FastAPI backend
Telegram bot
PostgreSQL
YooKassa
MoySklad
Meilisearch
R2/S3 media
Caddy
Prometheus/Grafana
CI/CD
release scripts
runbooks
pilot checklists
```

## Simplest start

```bash
python3 scripts/launch.py --mode local --with-search --with-workers
```

Or:

```bash
./scripts/start_simple.sh
```

## Before real production

Run:

```bash
python3 scripts/generate_env_todo.py
python3 scripts/connected_system_audit.py
python3 scripts/simplicity_score.py
python3 scripts/readiness_gate.py
python3 scripts/pilot_runner.py
```

## What is already connected

- Telegram entrypoint to Mini App.
- Catalog to product cards.
- Product cards to cart.
- Cart to checkout.
- Checkout to YooKassa payment.
- Webhook to paid order.
- Paid order to inventory writeoff.
- Paid order to fulfillment task.
- Loyalty/referral to customer profile.
- Refund to loyalty return.
- MoySklad to catalog sync.
- Delivery provider foundation to shipments.
- Media upload to processing jobs.
- Metrics to Prometheus/Grafana dashboards.

## What must be done with real accounts

- Set BotFather domain.
- Configure YooKassa test and production webhook.
- Connect real MoySklad token.
- Configure R2/S3 bucket.
- Replace legal pages.
- Sync real FLASHIN products.
- Run 20-order pilot.

## Stop adding architecture

From v50 onward, the next work should be:

```text
launch locally
connect credentials
run pilot
fix real breakages
deploy production
```

Do not add new abstract modules until the pilot exposes a real need.
