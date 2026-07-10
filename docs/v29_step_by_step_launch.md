# FLASHIN v29 — detailed step-by-step launch

## 1. Prepare server

Minimum:

```text
2 CPU
4 GB RAM
Ubuntu 22.04+
Docker
Docker Compose
```

## 2. Upload project

```bash
unzip flashin-miniapp-v29.zip
cd flashin-miniapp-v29
cp .env.example .env
```

## 3. Fill `.env`

Mandatory:

```env
TELEGRAM_BOT_TOKEN=
BOT_TOKEN=
JWT_SECRET=
ADMIN_EMAIL=
ADMIN_PASSWORD=
MINI_APP_URL=https://mini.flashin.store
API_PUBLIC_URL=https://api.flashin.store
YOOKASSA_SHOP_ID=
YOOKASSA_SECRET_KEY=
YOOKASSA_RETURN_URL=https://mini.flashin.store/payment-result
```

Use strong random values for:

```env
JWT_SECRET
ADMIN_PASSWORD
```

## 4. Run preflight

```bash
python scripts/preflight.py
```

## 5. Start stack

```bash
docker compose up --build
```

Services:

```text
Frontend Mini App: http://localhost:5173
Admin Panel:       http://localhost:5174
Backend API:       http://localhost:8000
Bot:               polling mode
PostgreSQL:        localhost:5432
```

## 6. Open admin

```text
http://localhost:5174
```

Login:

```text
ADMIN_EMAIL
ADMIN_PASSWORD
```

## 7. Import products

Use:

```text
sample_products_import.csv
```

or your real file with columns:

```text
sku,title,slug,price,size,variant_sku,stock_qty,brand,description,currency,category,gender,image_url,color,active
```

## 8. Connect Telegram

In BotFather:

```text
/setdomain
mini.flashin.store
```

Then open your bot and press Mini App button.

## 9. Configure YooKassa webhook

In YooKassa cabinet set webhook URL:

```text
https://api.flashin.store/api/payments/webhook/yookassa
```

Events:

```text
payment.succeeded
payment.canceled
```

## 10. Full test order

1. Open Mini App from Telegram.
2. Open product.
3. Select size.
4. Add to cart.
5. Apply promocode if needed.
6. Checkout.
7. Pay in YooKassa test mode.
8. Check backend order status.
9. Check inventory decreased.
10. Check notification queue.
11. Run worker if needed:

```bash
docker compose --profile workers up notification_worker
```

## 11. Production hardening

Before real launch:

```text
USE_CREATE_ALL=false
ENABLE_SEED=false
real domains
HTTPS
backup
monitoring
admin password changed
YooKassa production keys
```
