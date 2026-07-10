# Production launch runbook

## 1. Infrastructure

- Domain: `mini.flashin.store` for frontend.
- Domain: `api.flashin.store` for backend.
- Database: PostgreSQL 16.
- TLS: Cloudflare, Caddy, Nginx or platform-managed HTTPS.

## 2. Environment variables

Copy `.env.example` to `.env` and set:

```env
APP_ENV=production
FRONTEND_URL=https://mini.flashin.store
API_URL=https://api.flashin.store
MINI_APP_URL=https://mini.flashin.store
DATABASE_URL=postgresql+psycopg2://user:password@host:5432/flashin
SECRET_KEY=long-random-secret
CORS_ORIGINS=https://mini.flashin.store
BOT_TOKEN=botfather-token
PAYMENT_PROVIDER=yukassa
YUKASSA_SHOP_ID=shop-id
YUKASSA_SECRET_KEY=secret-key
```

## 3. BotFather

1. `/setdomain` → `mini.flashin.store`.
2. `/setdescription` → короткое описание магазина.
3. `/setabouttext` → что продаёт FLASHIN.
4. `/setuserpic` → логотип FLASHIN.

## 4. Smoke tests

- `GET https://api.flashin.store/health`
- `GET https://api.flashin.store/ready`
- `GET https://api.flashin.store/api/products`
- Open Mini App from bot.
- Add product to cart.
- Create order.
- Create payment.
- Check order in DB.

## 5. First release scope

Only launch:

- Catalog.
- Product card.
- Size selection.
- Cart.
- Checkout.
- Payment.

Do not launch loyalty, recommendations or heavy admin features before the purchase flow is stable.
