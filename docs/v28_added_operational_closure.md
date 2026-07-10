# FLASHIN v28 — operational closure layer

## Added

### Admin
- Admin user model.
- Admin login endpoint.
- Admin JWT.
- Product creation.
- Product active/inactive toggle.
- Variant stock update.
- Orders list.
- Order status update.
- Promocode creation.
- Notification queue view.

### Media
- Local media storage.
- Upload endpoint.
- File type validation.
- File size validation.
- Static `/media` mount.

### Orders
- Strict statuses.
- Admin status update.
- Tracking number.
- Order history for client.
- Refund request flow.

### Payments
- Idempotent YooKassa webhook events.
- Payment event log.
- Paid status only from webhook.
- Stock subtraction only after payment success.
- Reserved stock release after payment cancel.

### Promo codes
- Promo code model.
- Percent/fixed discount.
- Expiration.
- Usage limits.
- Cart application.
- Checkout transfer into order.

### Notifications
- Notification queue table.
- Queue message on paid order.
- Queue message on admin status update.
- Worker script to send pending notifications.

### Customer UX
- Wishlist.
- Restock subscription.
- Order history.
- Return request.

## What is still not perfect

1. Admin has backend API, but not full React visual dashboard.
2. Media storage is local. For production, replace with Cloudflare R2/S3.
3. Alembic migration is still intentionally safe/placeholder. Before public launch, autogenerate production migration.
4. YooKassa webhook authenticity should be additionally restricted at network/firewall level or verified through provider API call for high-risk environments.
5. Notification worker is pull-based; for high load use Celery/RQ/Arq.
