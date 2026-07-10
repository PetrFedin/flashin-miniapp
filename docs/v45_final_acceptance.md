# v45 Final Acceptance

## Acceptance principle

The system is accepted only when it can survive a real customer order without manual developer intervention.

## Critical path

```text
Telegram post
→ Mini App
→ product
→ cart
→ checkout
→ YooKassa
→ webhook
→ paid order
→ inventory writeoff
→ fulfillment task
→ notification
→ support/refund if needed
```

## Acceptance tests

1. Create order.
2. Pay order.
3. Verify webhook.
4. Verify stock.
5. Verify fulfillment.
6. Create refund.
7. Verify money refund.
8. Verify loyalty points return.
9. Verify support ticket.
10. Verify audit trail.

## Acceptance decision

- GO: all critical path tests pass.
- NO-GO: any payment, stock, fulfillment or refund test fails.
