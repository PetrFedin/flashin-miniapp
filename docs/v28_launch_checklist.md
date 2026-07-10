# FLASHIN v28 launch checklist

## Required settings

`.env`

```env
TELEGRAM_BOT_TOKEN=
JWT_SECRET=
ADMIN_EMAIL=
ADMIN_PASSWORD=
YOOKASSA_SHOP_ID=
YOOKASSA_SECRET_KEY=
MINI_APP_URL=
API_PUBLIC_URL=
```

## Local launch

```bash
cp .env.example .env
docker compose up --build
```

## Worker launch

```bash
docker compose --profile workers up --build
```

## BotFather

1. `/setdomain`
2. Set `mini.flashin.store`.
3. Open bot.
4. Press Mini App button.

## YooKassa

Set webhook:

```text
https://api.flashin.store/api/payments/webhook/yookassa
```

Events:
- `payment.succeeded`
- `payment.canceled`

## Admin

1. Login via `/api/admin/login`.
2. Upload images via `/api/media/upload`.
3. Create products via `/api/admin/products`.
4. Update stocks via `/api/admin/variants/{id}/stock`.
5. Create promocodes via `/api/admin/promocodes`.
6. Manage orders via `/api/admin/orders`.

## Full test

1. Open Mini App in Telegram.
2. Authenticate.
3. View catalog.
4. Open product.
5. Choose size.
6. Add to cart.
7. Apply promo.
8. Checkout.
9. Create payment.
10. Pay test order.
11. Receive webhook.
12. Order becomes paid.
13. Stock decreases.
14. Notification row is queued.
15. Worker sends notification.
16. Admin updates order to ready/shipped.
17. Client sees status in order history.
18. Client can request return.
