# FLASHIN v33 — post-launch architecture layer

## Added

### RBAC / permissions

New table:

```text
admin_role_permissions
```

New service:

```text
backend/services/rbac.py
```

Roles:

- owner;
- manager;
- support;
- warehouse.

### Support

New table:

```text
support_tickets
```

New endpoints:

```text
POST /api/support/tickets
GET  /api/support/tickets
GET  /api/support/admin/tickets
PATCH /api/support/admin/tickets/{id}
```

### Privacy / GDPR operations

New tables:

```text
privacy_requests
consent_records
```

New endpoints:

```text
POST /api/privacy/consent
POST /api/privacy/requests
GET  /api/privacy/requests
GET  /api/privacy/export
GET  /api/privacy/admin/requests
POST /api/privacy/admin/requests/{id}/process
```

### Webhook outbox

New table:

```text
webhook_outbox
```

Purpose:

- decouple checkout/payment from external integrations;
- retry failed outgoing webhooks;
- avoid blocking payment success flow.

New endpoints:

```text
GET  /api/outbox
POST /api/outbox/{id}/retry
```

New job:

```bash
python scripts/run_outbox_jobs.py
```

### Admin UI

Admin panel now shows:

- support tickets;
- privacy requests;
- webhook outbox;
- retry button;
- process privacy request button.

## Why this matters

v32 was pilot readiness. v33 adds post-launch survivability:

```text
customer problem -> support ticket
privacy request -> export/process
external integration fails -> outbox retry
admin access -> role-based permissions
```

Without this layer, launch can work, but operations become manual chaos.
