# What must be filled before launch

## Environment

```text
TELEGRAM_BOT_TOKEN
JWT_SECRET
ADMIN_EMAIL
ADMIN_PASSWORD
YOOKASSA_SHOP_ID
YOOKASSA_SECRET_KEY
MOYSKLAD_TOKEN
MEILISEARCH_MASTER_KEY
OUTBOX_SIGNING_SECRET
S3_ACCESS_KEY_ID
S3_SECRET_ACCESS_KEY
```

## BotFather

- Bot domain: `mini.flashin.store`
- Menu button opens Mini App.

## YooKassa

Webhook:

```text
https://api.flashin.store/api/payments/webhook/yookassa
```

Events:

```text
payment.succeeded
payment.canceled
```

## MoySklad

Check mapping:

- SKU/article;
- sizes;
- colors;
- categories;
- stock;
- prices;
- variants.

## Legal

Replace:

```text
frontend/public/legal/offer.html
frontend/public/legal/privacy.html
frontend/public/legal/returns.html
```

## Real content

- 5–10 real products;
- real images;
- real prices;
- real delivery rules;
- real return policy;
- real contacts.
