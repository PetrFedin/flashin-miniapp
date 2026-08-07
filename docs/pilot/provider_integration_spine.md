# Provider integration spine — pilot v24

This runbook binds the production order lifecycle to the external providers used
by the FLASHIN pilot. It does **not** mark live provider evidence as complete.
The signed pilot admission and P01–P20 checklist remain authoritative.

## Target path

`Telegram Mini App → FastAPI → YooKassa → payment webhook → Order → local inventory ledger → MoySklad command outbox → fulfillment → refund → refund webhook/reconciliation → inventory return → Telegram notification`

Local PostgreSQL state is authoritative for checkout and transaction safety.
External provider writes are durable asynchronous commands so temporary provider
outages cannot roll back a payment that has already been committed locally.

## Telegram

Required production values:

- `TELEGRAM_BOT_TOKEN`
- `BOT_TOKEN` for the notification worker, normally the same BotFather token
- `MINI_APP_URL=https://mini.flashin.store`
- `API_PUBLIC_URL=https://api.flashin.store`

The Mini App sends Telegram `initData`; the backend validates its Telegram HMAC
before issuing the application JWT. Do not save raw `initData` in pilot evidence.

Run the notification worker with the `workers` Compose profile. Notifications
are persisted before sending and are leased/retried by `bot.send_notifications`.
Order-paid, fulfillment/delivery status, and refund notifications use deterministic
event keys, so duplicate producer calls do not create duplicate messages.

## YooKassa

Required production values:

- `PAYMENT_PROVIDER=yookassa`
- `YOOKASSA_SHOP_ID`
- `YOOKASSA_SECRET_KEY`
- `YOOKASSA_RETURN_URL=https://mini.flashin.store/payment-result`

The project uses HTTP Basic Auth for the shop API. With this authentication mode,
YooKassa webhook subscriptions are configured in the YooKassa account under
Integration / HTTP notifications rather than through the Webhooks API.

Configure HTTPS notifications for the following application endpoints:

- payment events → `https://api.flashin.store/api/payments/webhook/yookassa`
- `refund.succeeded` → `https://api.flashin.store/api/returns/webhook/yookassa`

The endpoints intentionally do not trust the webhook body as the final source of
truth. They re-fetch the payment/refund from YooKassa and verify provider id,
status, amount, currency, and local binding before committing state.

YooKassa requires an HTTP 200 acknowledgement for a successfully accepted
notification and retries non-200 responses. The refund endpoint therefore
returns 409 for a provider/local integrity mismatch so the pilot fails closed.

## Local inventory

Checkout reserves local `ProductVariant` stock. Successful payment converts the
reservation into a durable `commit` movement and decreases `stock_qty`.

Refund policy:

- partial financial refund: do not fabricate item-level inventory changes;
- full cumulative refund: restore the complete order quantity once with a durable
  `return` movement;
- duplicate refund webhook/reconciliation: no additional stock restoration;
- a partially recorded return movement is an integrity conflict and requires
  manual review.

This policy exists because the current return request records a refund amount but
not per-line item quantities. Item-level partial returns require a separate data
model before they can safely affect stock.

## MoySklad outbound documents

Enable only after the target account IDs are known:

```env
MOYSKLAD_ORDER_EXPORT_ENABLED=true
MOYSKLAD_BASE_URL=https://api.moysklad.ru/api/remap/1.2
MOYSKLAD_TOKEN=...
MOYSKLAD_SALE_PRICE_TYPE=...
MOYSKLAD_ORGANIZATION_ID=...
MOYSKLAD_AGENT_ID=...
MOYSKLAD_STORE_ID=...
MOYSKLAD_DELIVERY_SERVICE_ID=...
```

A login/password pair may be used instead of a token, but both values must be
present together. Production refuses to start with outbound export enabled and
an incomplete configuration.

Document mapping:

1. paid order → `customerorder`;
2. shipment transition to `shipped` → `demand`;
3. full cumulative refund after shipment/delivery → `salesreturn` bound to the
   completed demand.

Every create uses a deterministic UUID `syncId`. MoySklad defines `syncId` as an
idempotency mechanism for entity creation: resending a request after an uncertain
network failure returns the previously created entity rather than duplicating it.

Order positions are rendered in kopecks. Promo/loyalty discounts are allocated
across merchandise lines and the generated position total must equal
`Order.total_amount` exactly. Paid delivery is represented by the configured
MoySklad service entity. Any mapping or money mismatch becomes `review_required`
instead of sending a wrong accounting document.

External commands live in `provider_commands` and have these states:

- `pending`
- `processing` with a lease
- `sent`
- `failed` after bounded retries
- `review_required` for deterministic configuration/data problems

Run the worker:

```bash
docker compose --profile workers up -d provider_command_jobs notification_worker
```

or one batch for operations/debugging:

```bash
python scripts/run_provider_command_jobs.py --once
```

The scheduler also processes provider commands once per minute under a PostgreSQL
advisory lock. `provider_command_jobs` polls by default every 15 seconds; configure
`PROVIDER_COMMAND_POLL_SECONDS` only within 5–300 seconds.

### Pilot safety coupling

The provider worker reconciles terminal MoySklad commands against orders admitted
to the **current** pilot run before each claim cycle and immediately after a
command becomes terminal. A current-run order with `review_required` or terminal
`failed` stops the pilot runtime and therefore closes new pilot checkout until an
operator reconciles the provider state and explicitly starts a new admitted run.

This coupling is intentionally bounded:

- `pending` and `processing` retries do not stop the pilot;
- commands for historical runs or orders outside the pilot do not stop it;
- non-order commands do not stop it;
- providers outside the current pilot-critical set do not stop it;
- stop reasons contain only bounded status categories and never command payloads,
  provider error bodies, external IDs, or idempotency keys.

Stopping pilot admission does not discard the durable provider command. Workers
continue their normal reconciliation path so already-paid orders can still reach
a consistent external state. If a worker dies after persisting a terminal command
but before persisting the pilot stop, the next worker cycle performs the same
terminal sweep before claiming more work.

## Fulfillment

Successful payment creates exactly one fulfillment task. The guarded lifecycle is:

`new → picking → packed → ready`

Every picklist item must be fully picked before `packed`. A ready order can create
one shipment. Delivery lifecycle:

`created → shipped → delivered`

The `ready → shipped` order transition enqueues the MoySklad demand in the same
local database transaction that updates shipment/order state.

## Refund completion

Admin refund approval creates or reuses a YooKassa refund using a deterministic
idempotency key. If YooKassa returns a non-terminal state, the reconciliation job
continues by re-fetching the refund. `refund.succeeded` can also finalize through
the dedicated webhook endpoint.

On full completion the same database transaction:

- changes order/payment status to `refunded`;
- reverses loyalty effects exactly once;
- restores local sold stock exactly once;
- queues a customer Telegram notification;
- if the order was shipped/delivered, enqueues a MoySklad sales return.

A sales return waits/retries if its demand dependency has not yet been sent; that
transient ordering condition does not require operator review.

## CI evidence versus live evidence

`backend/tests/test_provider_integration_spine_smoke.py` invokes
`scripts/provider_integration_spine_smoke.py`. It uses real PostgreSQL/domain
transactions from payment settlement through fulfillment, delivery, full refund,
stock restoration, notifications, and the durable MoySklad command worker. Only
the external MoySklad HTTP boundary is replaced by a deterministic local fake.

`backend/tests/test_refund_webhooks.py` verifies that a spoofed webhook amount is
ignored in favor of an authoritative provider re-fetch and that a duplicate
`refund.succeeded` is idempotent.

`backend/tests/test_provider_command_pilot_safety.py` verifies that terminal
MoySklad commands for current-run pilot orders stop admission, while transient
retries and historical/non-pilot/noncritical commands do not.

These are internal CI gates only. Before real money, collect separate live evidence
for Telegram signed auth, YooKassa sandbox payment/return/webhooks, MoySklad
created documents/stock, and Telegram delivery, then complete the signed P01–P20
launch checklist.

## Pilot deployment order

1. protect `main` with all six mandatory CI checks;
2. deploy the exact signed release SHA;
3. provision DNS/TLS and secrets outside Git;
4. configure YooKassa HTTP notifications;
5. set and verify MoySklad account/entity IDs;
6. start API, bot, notification worker, provider command worker, scheduler and
   monitoring;
7. run migrations through head (`0026_inventory_return_movement` or later);
8. verify provider connectivity without storing secrets in evidence;
9. execute P01–P20 on the deployed environment;
10. create signed lifecycle, governance and launch-checklist evidence;
11. arm the 20-order pilot only after final admission returns `go=true`.
