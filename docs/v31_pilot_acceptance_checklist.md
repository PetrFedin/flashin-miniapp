# FLASHIN v31 pilot acceptance checklist

Before pilot, complete every item.

## Infrastructure

- [ ] `mini.flashin.store` opens Mini App.
- [ ] `admin.flashin.store` opens Admin.
- [ ] `api.flashin.store/health` returns OK.
- [ ] `api.flashin.store/ready` returns OK.
- [ ] HTTPS works on all domains.
- [ ] Caddy logs are clean.
- [ ] PostgreSQL backup script works.
- [ ] Restore was tested once.

## Telegram

- [ ] BotFather domain configured.
- [ ] Bot opens Mini App.
- [ ] Telegram auth succeeds.
- [ ] MainButton works.
- [ ] Product deeplink opens correct product.

## Catalog

- [ ] Product CSV import works.
- [ ] Image upload works.
- [ ] Product appears in Mini App.
- [ ] Size availability is correct.
- [ ] Low-stock appears in admin.

## Cart and checkout

- [ ] Add to cart works.
- [ ] Promo code works.
- [ ] Delivery price is added.
- [ ] Checkout creates order.
- [ ] Inventory reserved after checkout.

## Payment

- [ ] YooKassa payment link is created.
- [ ] Test payment succeeded.
- [ ] Webhook received.
- [ ] Order becomes paid.
- [ ] Stock decreases.
- [ ] Duplicate webhook does not double-decrease stock.
- [ ] Canceled payment releases reserve.

## Operations

- [ ] Admin can change order status.
- [ ] Audit log records action.
- [ ] Notification queue receives message.
- [ ] Notification worker sends message.
- [ ] Abandoned cart job queues message.
- [ ] Inventory snapshot job runs.

## Refunds

- [ ] Return request can be created.
- [ ] Admin refund approve calls YooKassa.
- [ ] Order becomes refunded.
- [ ] Refund ID saved.

## Go / No-go

Go only if all critical flows pass with real Telegram and YooKassa test mode.
