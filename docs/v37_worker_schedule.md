# v37 worker schedule

Recommended cron:

```cron
*/5 * * * * cd /opt/flashin && docker compose --profile workers run --rm campaign_jobs
*/10 * * * * cd /opt/flashin && docker compose --profile workers run --rm notification_worker
*/30 * * * * cd /opt/flashin && docker compose --profile workers run --rm ops_jobs
*/30 * * * * cd /opt/flashin && docker compose --profile workers run --rm moysklad_sync
*/5 * * * * cd /opt/flashin && docker compose --profile workers run --rm outbox_jobs
*/5 * * * * cd /opt/flashin && docker compose --profile workers run --rm sla_jobs
0 3 * * * cd /opt/flashin && scripts/backup_postgres.sh
```

Before enabling automation, test every worker manually once.
