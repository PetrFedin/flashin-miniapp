# 30-Minute Launch Plan

## Minute 0–5

```bash
unzip flashin-miniapp-v49.zip
cd flashin-miniapp-v49
python3 scripts/launch.py --mode local --with-search --with-workers
```

## Minute 5–10

Open:

```text
http://localhost:5173
http://localhost:5174
http://localhost:8000/docs
```

## Minute 10–15

Fill real test credentials in `.env`:

```text
TELEGRAM_BOT_TOKEN
YOOKASSA_SHOP_ID
YOOKASSA_SECRET_KEY
MOYSKLAD_TOKEN
```

Restart:

```bash
docker compose restart
```

## Minute 15–20

Run:

```bash
python3 scripts/check_integrations.py
python3 scripts/connected_system_audit.py
python3 scripts/simplicity_score.py
```

## Minute 20–25

Sync products:

```bash
docker compose --profile workers run --rm moysklad_sync
```

Check admin:

```text
Products
MoySklad conflicts
Stock
Images
Prices
```

## Minute 25–30

Run pilot sheet:

```bash
python3 scripts/generate_20_order_pilot_sheet.py
```

Start testing first order.
