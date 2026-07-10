# FLASHIN v31 — operations and observability depth

## Added

### Audit log

New table:

```text
audit_logs
```

Admin actions can now be logged:

- order status changes;
- promo creation;
- stock adjustments.

Admin panel shows recent audit logs.

### Inventory control

New tables:

```text
inventory_adjustments
inventory_snapshots
```

Added:

- stock adjustment log;
- inventory snapshot job;
- low-stock view in admin;
- low stock threshold from env.

### Abandoned carts

Cart now has:

```text
abandoned_notified_at
```

Added:

- admin endpoint to list abandoned carts;
- admin action to queue abandoned cart Telegram notifications;
- scheduled job script.

### Sentry

`SENTRY_DSN` added. If configured, backend initializes Sentry.

### Ops jobs

Script:

```bash
python scripts/run_ops_jobs.py
```

Docker profile:

```bash
docker compose --profile workers up ops_jobs
```

### Admin operations dashboard

Admin now shows:

- low stock;
- abandoned carts;
- audit logs;
- buttons to queue notifications and inventory snapshot.

## Recommended schedule

Cron:

```cron
*/30 * * * * cd /opt/flashin && docker compose --profile workers run --rm ops_jobs
*/10 * * * * cd /opt/flashin && docker compose --profile workers run --rm notification_worker
0 3 * * * cd /opt/flashin && scripts/backup_postgres.sh
```

## Remaining limitations

1. Abandoned cart logic sends one notification per cart; more advanced CRM sequencing can be added later.
2. Rate limit is still in-memory; use Redis for multi-node production.
3. Audit log covers critical admin actions but not every endpoint yet.
4. Inventory reconciliation with external ERP still requires a connector if your real stock lives outside PostgreSQL.
