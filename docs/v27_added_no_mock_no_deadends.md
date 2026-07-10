# FLASHIN Mini App v27 — что добавлено

## Цель v27

v27 убирает ключевые тупики v26 и переводит проект из UX-каркаса в связанный MVP-контур:

- Telegram auth через проверку `initData`.
- JWT-сессия клиента.
- Server-side корзина.
- Checkout из корзины.
- Резервирование остатков при создании заказа.
- YooKassa payment creation без fake success.
- YooKassa webhook.
- Списание остатка только после `payment.succeeded`.
- Возврат резерва при `payment.canceled`.
- Analytics events.
- Deeplink товара через `?product=ID`.
- Telegram MainButton для корзины и покупки.

## Важно

В проекте больше нет логики "считать заказ оплаченным без провайдера".
Если YooKassa не настроена, endpoint оплаты вернёт ошибку конфигурации. Это правильно: лучше явная ошибка, чем фейковая продажа.

## Что ещё остаётся перед public launch

1. Защитить admin routes отдельной ролью.
2. Подключить Cloudflare R2/S3 для фото.
3. Настроить реальный домен `mini.flashin.store`.
4. Настроить реальный домен `api.flashin.store`.
5. Указать YooKassa webhook URL: `https://api.flashin.store/api/payments/webhook/yookassa`.
6. Загрузить реальные товары через admin/import API.
7. Провести тесты на iOS/Android/Desktop Telegram.
8. Прогнать 10 тестовых заказов через YooKassa test mode.
