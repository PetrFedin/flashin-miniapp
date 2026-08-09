# FLASHIN pilot provider integration setup

This runbook configures the external edge of the controlled pilot without weakening the existing signed admission process.

## Target path

```text
Telegram Mini App
  -> signed Telegram initData -> POST /api/auth/telegram
  -> cart -> POST /api/orders/checkout (Idempotency-Key)
  -> POST /api/payments
  -> YooKassa confirmation_url
  -> POST /api/webhooks/yookassa
  -> authoritative YooKassa GET verification
  -> payment settlement
       -> inventory reserve -> commit
       -> fulfillment task
       -> MoySklad customer-order command
       -> Telegram notification queue
  -> fulfillment -> shipment
       -> MoySklad demand command
       -> Telegram status notification queue
  -> return request -> admin approval -> YooKassa refund
  -> POST /api/webhooks/yookassa (refund.succeeded)
  -> authoritative YooKassa refund GET verification
  -> full refund
       -> inventory return movement
       -> MoySklad sales-return command
       -> loyalty reversal
       -> Telegram refund notification queue
```

The scheduler executes durable provider-command/reconciliation jobs. The dedicated Telegram notification worker sends queued customer notifications with lease/retry state.

## 1. Production environment

Start from `.env.production.example`. The pilot wiring requires at minimum:

```dotenv
APP_ENV=production
MINI_APP_URL=https://mini.flashin.store
API_PUBLIC_URL=https://api.flashin.store
ADMIN_URL=https://admin.flashin.store

TELEGRAM_BOT_TOKEN=<BotFather token>
BOT_TOKEN=<same token>

YOOKASSA_SHOP_ID=<shop id>
YOOKASSA_SECRET_KEY=<secret key>
YOOKASSA_RETURN_URL=https://mini.flashin.store/payment-result
YOOKASSA_WEBHOOK_URL=https://api.flashin.store/api/webhooks/yookassa

MOYSKLAD_ORDER_EXPORT_ENABLED=true
MOYSKLAD_TOKEN=<preferred access token>
# Or MOYSKLAD_LOGIN + MOYSKLAD_PASSWORD instead of token.
MOYSKLAD_ORGANIZATION_ID=<uuid>
MOYSKLAD_AGENT_ID=<uuid>
MOYSKLAD_STORE_ID=<uuid>
MOYSKLAD_DELIVERY_SERVICE_ID=<uuid if paid delivery is exported>

SCHEDULER_ENABLED=true
PILOT_RUNTIME_ENFORCED=true
```

Never commit live tokens, passwords or YooKassa secrets. `PILOT_GITHUB_TOKEN` must also stay outside `.env` because Compose injects `.env` into application containers.

Run the side-effect-free topology check before provider probes:

```bash
python3 scripts/provider_wiring_preflight.py --env .env
```

A non-zero exit is pilot NO-GO.

## 2. Telegram

1. In BotFather, set the Mini App/web app URL to the production `MINI_APP_URL`.
2. Use the same bot token in `TELEGRAM_BOT_TOKEN` and compatibility alias `BOT_TOKEN`.
3. Do not trust `initDataUnsafe` for authentication. FLASHIN authenticates using the raw `Telegram.WebApp.initData` and server-side validation.
4. Restrict pilot checkout to the signed runtime allowlist; a valid Telegram identity alone must not bypass the first-20-order gate.

Official reference: https://core.telegram.org/bots/webapps

Provider identity probe:

```bash
python3 scripts/check_telegram_bot.py
```

The probe verifies the bot credential with Telegram `getMe`; it does not replace an actual Mini App launch and signed-initData lifecycle scenario.

## 3. YooKassa

FLASHIN authenticates its **outgoing YooKassa API requests** with HTTP Basic Auth using shop id + secret key. For HTTP Basic Auth merchant integrations, the notification subscription itself is configured in the YooKassa Personal Area. The incoming YooKassa callback is not required to carry the merchant's Basic Auth credentials and FLASHIN does not depend on such a header.

In YooKassa Personal Area -> Integration -> HTTP notifications configure exactly:

```text
https://api.flashin.store/api/webhooks/yookassa
```

Enable these events for the pilot:

- `payment.succeeded`
- `payment.canceled`
- `payment.waiting_for_capture` (supported even though the default FLASHIN payment uses `capture=true`)
- `refund.succeeded`

The legacy endpoints `/api/payments/webhook/yookassa` and `/api/returns/webhook/yookassa` stay available only for migration/rollback compatibility. Do not configure them as separate production notification URLs.

YooKassa requires HTTPS notification URLs on port 443 or 8443 and TLS 1.2+. FLASHIN answers HTTP 200 only after the callback was accepted/processed. For callback authenticity and freshness, both payment and refund processors re-fetch the current provider object using the authenticated YooKassa API before mutating local money/order state; webhook body fields are not treated as authoritative. An ingress/proxy IP allowlist may be added as defense in depth, but it must not replace the authoritative provider read.

Official references:

- https://yookassa.ru/developers/using-api/webhooks
- https://yookassa.ru/developers/payment-acceptance/getting-started/payment-process
- https://yookassa.ru/developers/payment-acceptance/after-the-payment/refunds

Live provider probe (creates one controlled 1.00 RUB pending payment):

```bash
make provider-probes
make check-integrations
```

Do not run the side-effectful probe repeatedly to compensate for configuration uncertainty. Fix the configuration, then re-run with the existing signed-evidence rules.

## 4. MoySklad

FLASHIN uses MoySklad JSON API 1.2. Token auth is preferred; Basic Auth is supported as a fallback.

The outbound document chain is durable and idempotent:

1. paid order -> `moysklad.customer_order.create`;
2. shipped order -> `moysklad.demand.create`;
3. completed full refund after shipment/delivery -> `moysklad.sales_return.create`.

A sales return is not sent until the corresponding demand was successfully created. Permanent mapping/configuration errors move provider commands to review instead of blind infinite retries.

Each sold product/variant used in the pilot must have an unambiguous MoySklad assortment mapping. The target account must provide organization, counterparty/agent and store identifiers. Paid delivery additionally needs a mapped delivery service.

Official API reference: https://dev.moysklad.ru/doc/api/remap/1.2/

Read-only provider probe:

```bash
python3 scripts/check_moysklad.py
```

## 5. Workers required for the pilot

Production deployment must keep these paths live:

- `bot` — Telegram Mini App entry/bot process;
- `notification_worker` — sends DB notifications to Telegram with lease/retry;
- `scheduler` — executes provider commands, refund reconciliation, outbox/events, SLA and MoySklad sync;
- `backend` — webhook/API/order domain;
- PostgreSQL — source of truth for orders, payments, inventory ledger, provider commands and notifications.

The scheduler is the production orchestrator; the individual worker-profile services remain useful for isolated operations/tests but are not required to run alongside the scheduler for the same scheduled jobs.

## 6. Internal E2E gates

The required `integrated-e2e` browser job drives one PostgreSQL order through:

1. signed Telegram Mini App authentication;
2. cart + promo + idempotent checkout;
3. YooKassa-style `pending` payment;
4. browser confirmation redirect;
5. duplicate `payment.succeeded` callbacks through `/api/webhooks/yookassa`;
6. authoritative provider GET and settlement;
7. stock `reserve -> commit`, fulfillment task and MoySklad customer-order command creation;
8. pick/pack/ready, shipment and MoySklad demand command creation;
9. delivered order;
10. customer return request;
11. admin full-refund approval producing a pending provider refund;
12. duplicate `refund.succeeded` callbacks through the same canonical webhook;
13. stock `return`, loyalty reversal, MoySklad sales-return command creation and one refund notification.

The test asserts that duplicate callbacks do not duplicate inventory return movements, provider commands or deterministic refund notifications. Its YooKassa HTTP boundary is deterministic/test-only; Telegram notification delivery and MoySklad HTTP dispatch are not claimed by this browser test.

A second mandatory backend CI gate, `scripts/provider_integration_spine_smoke.py`, takes the durable MoySklad commands through the real PostgreSQL provider-command worker and validates `customerorder -> demand -> salesreturn`, external IDs and payload/link relationships. Only the remote MoySklad HTTP boundary is replaced there. Notification delivery lease/retry behavior is covered by the separate mandatory notification-delivery smoke.

Together these gates prove the internal chain without pretending that live Telegram/YooKassa/MoySklad provider calls occurred. Live lifecycle evidence remains mandatory before runtime admission.

## 7. Live pilot evidence

Before first real checkout:

```bash
python3 scripts/provider_wiring_preflight.py --env .env
make validate-env
make readiness-gate
make provider-probes
make check-integrations
make pilot-gate
```

Then complete the signed lifecycle scenarios from `docs/pilot/live_pilot_runner.json` and `docs/pilot/live_lifecycle_evidence.md`.

For a completed real refunded order, the read-only terminal verifier can be run with operator-provided identifiers:

```bash
RUN_REAL_LIFECYCLE_E2E=1 \
API_BASE=https://api.flashin.store \
CUSTOMER_TOKEN=<short-lived customer token> \
ADMIN_TOKEN=<short-lived admin token> \
E2E_ORDER_ID=<order id> \
E2E_VARIANT_ID=<variant id> \
E2E_EXPECTED_STOCK_QTY=<expected restored stock> \
pytest -q backend/tests/e2e/test_order_payment_refund_flow.py
```

The verifier is read-only. It checks the final order/refund/fulfillment/stock/notification state plus provider diagnostics; it does not create another payment or refund.

## 8. Immediate stop conditions

Stop the pilot runtime if any of these occurs:

- webhook amount/currency/order reference differs from authoritative provider data;
- duplicate callback creates a second money, inventory or notification effect;
- payment succeeds after local cancellation and enters review;
- inventory becomes negative or `reserved_qty > stock_qty`;
- MoySklad command enters `review_required` for a pilot order;
- refund succeeds at YooKassa but local order/refund/stock state does not converge;
- Telegram notification delivery is failed/exhausted;
- scheduler, notification worker, `/ready`, database or signed pilot evidence becomes unavailable.

Use:

```bash
make pilot-runtime-stop REASON='precise incident reason'
```

Do not resume until the incident is resolved and affected release/evidence/admission artifacts have been regenerated according to the main pilot runbook.
