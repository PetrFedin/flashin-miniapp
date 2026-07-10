# Disaster Recovery

## PostgreSQL failure

1. Stop application writes.
2. Restore latest backup:
   ```bash
   scripts/restore_postgres.sh backups/latest.sql.gz
   ```
3. Run healthcheck:
   ```bash
   make health
   ```

## YooKassa failure

1. Keep orders in `payment_created`.
2. Do not commit inventory as sold.
3. Retry payment creation after provider recovery.
4. Use payment events table for audit.

## Telegram failure

1. Backend remains available.
2. Do not delete pending notifications.
3. Notification worker retries later.

## MoySklad failure

1. Continue selling local stock.
2. Pause scheduled sync.
3. Review `moysklad_conflicts`.
4. Run manual sync after recovery.

## CDN/R2 failure

1. Existing cached images may still work.
2. Temporarily switch `MEDIA_STORAGE=local` only if needed.
3. Do not rebuild media URLs without backup.

## Full rollback

1. Stop app:
   ```bash
   docker compose down
   ```
2. Restore DB.
3. Deploy previous release archive.
4. Run healthcheck.
