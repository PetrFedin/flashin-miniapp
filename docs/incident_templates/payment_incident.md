# Incident: Payment / YooKassa

## Symptoms

- Payment link does not open.
- Webhook is not received.
- Order stays unpaid.
- Duplicate webhook changed order incorrectly.

## Immediate actions

1. Check YooKassa dashboard.
2. Check backend logs:
   ```bash
   docker compose logs -f backend
   ```
3. Check payment events in admin/API.
4. Pause public announcements if payment is fully broken.
5. Do not manually decrement stock until payment is confirmed.

## Checks

```bash
python3 scripts/check_yookassa_test.py
curl https://api.flashin.store/health
```

## Recovery

- If webhook failed, replay from YooKassa dashboard if available.
- If order is paid in YooKassa but unpaid locally, verify provider payment id before manual update.
- If duplicate webhook issue appears, inspect `payment_events`.

## Customer message

"Оплата временно проверяется. Мы видим заказ и вернёмся с подтверждением после сверки платежа."
