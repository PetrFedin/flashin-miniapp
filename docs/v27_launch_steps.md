# Как запустить v27 локально

## 1. Распаковать архив

```bash
unzip flashin-miniapp-v27.zip
cd flashin-miniapp-v27
```

## 2. Создать env

```bash
cp .env.example .env
```

Заполнить:

```env
TELEGRAM_BOT_TOKEN=токен_из_BotFather
JWT_SECRET=длинная_случайная_строка
YOOKASSA_SHOP_ID=тестовый_shop_id
YOOKASSA_SECRET_KEY=тестовый_secret_key
MINI_APP_URL=https://ваш-туннель-frontend
API_PUBLIC_URL=https://ваш-туннель-backend
ENABLE_SEED=true
```

`ENABLE_SEED=true` можно использовать только для локального теста. В production ставить `false`.

## 3. Запустить Docker

```bash
docker compose up --build
```

Откроются:

```text
frontend: http://localhost:5173
backend:  http://localhost:8000
docs:     http://localhost:8000/docs
```

## 4. Проверить backend

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready
curl http://localhost:8000/api/products
```

## 5. Подключить Mini App к Telegram

Через BotFather:

```text
/setdomain
ваш HTTPS-домен frontend
```

Потом в `.env`:

```env
MINI_APP_URL=https://mini.flashin.store
```

Перезапустить bot.

## 6. Проверить сценарий

1. Открыть бота.
2. Нажать "Открыть магазин".
3. Открыть товар.
4. Выбрать размер.
5. Добавить в корзину.
6. Открыть корзину через MainButton.
7. Оформить заказ.
8. Перейти в оплату YooKassa.
9. Получить webhook.
10. Проверить, что заказ стал `paid`, а остаток списался.

## 7. Production

Минимум:

```text
mini.flashin.store -> frontend
api.flashin.store  -> backend
PostgreSQL managed
HTTPS
YooKassa webhook
BotFather domain
Monitoring
Backups
```
