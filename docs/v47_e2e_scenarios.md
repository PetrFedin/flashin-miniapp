# v47 E2E scenarios

## Critical flow

```text
cart
checkout
payment webhook
fulfillment task
refund
loyalty return
```

## Manual prerequisites

- real or test Telegram initData;
- YooKassa test credentials;
- one active product;
- one variant in stock;
- admin token.

## Scenarios

1. Add product to cart.
2. Apply promo.
3. Apply loyalty points.
4. Create order.
5. Create payment.
6. Simulate/receive payment webhook.
7. Verify order paid.
8. Verify stock decremented.
9. Verify fulfillment task created.
10. Create refund request.
11. Approve refund.
12. Verify loyalty points returned.
