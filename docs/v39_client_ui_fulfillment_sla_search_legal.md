# FLASHIN v39 — client UI, pick-list, SLA worker, Meilisearch settings, legal pages

## Added

### Client Mini App UI

The Mini App now exposes existing backend capabilities:

- profile;
- CRM segment;
- loyalty balance;
- loyalty transactions;
- referral code;
- referral code input in cart;
- loyalty redemption in cart;
- product search;
- looks / complete-the-look list;
- support ticket creation;
- support ticket list;
- privacy export;
- privacy delete request;
- customer timeline;
- size helper.

### Fulfillment pick-list

Endpoints:

```text
GET   /api/fulfillment/tasks/{task_id}/picklist
PATCH /api/fulfillment/task-items/{task_item_id}
```

This supports:

- item-level picking;
- picked quantity;
- issue per item;
- print-friendly pick-list payload.

### SLA worker

New job:

```bash
python scripts/run_sla_jobs.py
```

It marks overdue SLA events as:

```text
overdue
```

Docker profile:

```bash
docker compose --profile workers run --rm sla_jobs
```

### Meilisearch settings

New service:

```text
backend/services/meili_settings.py
```

New script:

```bash
python scripts/configure_meilisearch.py
```

New endpoint:

```text
POST /api/search/admin/configure-meili
```

It configures:

- searchable attributes;
- filterable attributes;
- sortable attributes;
- ranking rules.

### Legal pages

Added frontend legal files:

```text
frontend/public/legal/offer.html
frontend/public/legal/privacy.html
frontend/public/legal/returns.html
```

These are placeholders that must be replaced by lawyer-approved texts before launch.

### Tests

Added smoke tests for:

- Meilisearch settings;
- SLA job;
- frontend API surface.

## Why v39 matters

v38 had backend power. v39 makes that power visible and operational:

```text
customer can see profile/points/referral
customer can search and request support/privacy actions
warehouse can use pick-list
SLA can become overdue automatically
Meilisearch can be configured consistently
legal pages exist in the package
```
