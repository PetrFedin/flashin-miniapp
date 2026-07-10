# FLASHIN v38 — fulfillment, SLA, signed outbox, profile

## Added

### Fulfillment

New tables:

```text
fulfillment_tasks
fulfillment_task_items
```

Created automatically after `payment.succeeded`.

Admin endpoints:

```text
GET   /api/fulfillment/tasks
PATCH /api/fulfillment/tasks/{id}
```

### SLA

New table:

```text
sla_events
```

Created after paid order.

Endpoint:

```text
GET /api/fulfillment/sla
```

### Signed webhook destinations

New table:

```text
webhook_destinations
```

Outgoing outbox requests now include:

```text
X-Flashin-Signature
```

Signature is HMAC SHA256 over JSON body.

Endpoints:

```text
GET  /api/webhook-destinations
POST /api/webhook-destinations
```

### Customer profile

Endpoint:

```text
GET /api/profile
```

Returns:

- customer;
- CRM profile;
- referral code;
- loyalty points.

### Admin UI

Admin now includes:

- fulfillment tasks;
- SLA events;
- webhook destinations.

## Why it matters

v37 made money logic safer. v38 adds warehouse/process control:

```text
paid order -> fulfillment task -> picking/packing/ready
paid order -> SLA event
paid order -> signed external webhook
customer -> profile endpoint
```
