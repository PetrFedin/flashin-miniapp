# v38 fulfillment runbook

## Paid order flow

1. YooKassa sends `payment.succeeded`.
2. Backend verifies payment.
3. Order becomes paid.
4. Inventory is decremented.
5. Fulfillment task is created.
6. SLA event is created.
7. Telegram notification is queued.
8. Outbox event is queued.

## Fulfillment statuses

Recommended:

```text
new
picking
packed
ready
handed_to_courier
completed
issue
```

## Webhook signing

Receiver should verify:

```text
X-Flashin-Signature
```

Using HMAC SHA256 with the shared secret.
