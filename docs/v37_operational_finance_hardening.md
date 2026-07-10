# FLASHIN v37 — operational and finance hardening

## Added

### Loyalty full lifecycle

New table:

```text
loyalty_redemption_holds
```

Flow:

```text
cart applies points
↓
hold is created
↓
checkout stores redemption on order
↓
payment.succeeded commits redemption
↓
refund returns redeemed points
```

This prevents accidental point loss and double redemption.

### Campaign scheduling

Marketing campaigns now support:

```text
scheduled_at
status=scheduled
```

New job:

```bash
python scripts/run_campaign_jobs.py
```

Docker worker:

```bash
docker compose --profile workers run --rm campaign_jobs
```

### Stock reconciliation

New table:

```text
stock_reconciliation_logs
```

New API:

```text
GET /api/reconciliation/stock
```

This allows reporting differences between local stock and external stock before applying changes.

### Customer timeline in admin

Admin can open customer timeline directly from customer list.

Endpoint:

```text
GET /api/timeline/admin/customers/{customer_id}
```

### More tests

Added import/smoke tests for:

- reconciliation service;
- campaign jobs.

## Why v37 matters

v36 made the architecture stronger. v37 makes it safer operationally:

```text
loyalty cannot silently disappear
refund returns points
campaigns can be scheduled
stock conflicts are visible
customer history is visible to support/admin
```
