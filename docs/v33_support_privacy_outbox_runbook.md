# v33 support/privacy/outbox runbook

## Support tickets

Customer creates:

```text
POST /api/support/tickets
```

Admin opens:

```text
GET /api/support/admin/tickets
```

Admin updates:

```text
PATCH /api/support/admin/tickets/{id}
```

Recommended statuses:

```text
open
in_progress
waiting_customer
resolved
closed
```

## Privacy requests

Customer can request:

```text
export
delete
consent_withdrawal
```

Export endpoint:

```text
GET /api/privacy/export
```

Admin processing:

```text
POST /api/privacy/admin/requests/{id}/process
```

## Outbox

Outbox is for external integrations and retryable events.

Run manually:

```bash
python scripts/run_outbox_jobs.py
```

Cron:

```cron
*/5 * * * * cd /opt/flashin && docker compose --profile workers run --rm backend python scripts/run_outbox_jobs.py
```

Recommended future destinations:

- CRM;
- warehouse;
- Google Sheets;
- internal analytics;
- external notification systems.

## Important

`internal://order-paid` is a placeholder destination for internal events. It is intentionally not sent to internet services. Before adding real external destinations, configure a valid HTTPS URL.
