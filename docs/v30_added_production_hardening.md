# FLASHIN v30 — production hardening layer

## Added in v30

### Production reverse proxy

File:

```text
deploy/Caddyfile
```

Domains:

```text
mini.flashin.store  -> frontend
admin.flashin.store -> admin frontend
api.flashin.store   -> backend API
```

Includes:

- HTTPS by Caddy;
- gzip/zstd;
- upload body limit;
- security headers.

### Backups

Files:

```text
scripts/backup_postgres.sh
scripts/restore_postgres.sh
```

Usage:

```bash
scripts/backup_postgres.sh
scripts/restore_postgres.sh backups/flashin_YYYYMMDD_HHMMSS.sql.gz
```

### Rate limiting

File:

```text
backend/middleware/rate_limit.py
```

Protects:

- `/api/auth/telegram`;
- `/api/admin/login`;
- general API paths.

For multi-container production, replace in-memory counters with Redis.

### Media storage

File:

```text
backend/services/media_storage.py
```

Supports:

```text
MEDIA_STORAGE=local
MEDIA_STORAGE=s3
MEDIA_STORAGE=r2
```

For Cloudflare R2 set:

```env
MEDIA_STORAGE=r2
S3_ENDPOINT_URL=https://<account_id>.r2.cloudflarestorage.com
S3_BUCKET=flashin-media
S3_REGION=auto
S3_ACCESS_KEY_ID=
S3_SECRET_ACCESS_KEY=
MEDIA_PUBLIC_BASE_URL=https://cdn.flashin.store
```

### Refund through YooKassa

Added:

```text
POST /api/returns/admin/approve
```

This creates a YooKassa refund and updates return/order statuses.

### Delivery

Added:

```text
DeliveryZone model
GET /api/delivery/zones
POST /api/delivery/zones
```

Checkout now includes delivery price in order total.

### Admin UI

Admin now includes:

- product form;
- image upload;
- variant/size rows;
- CSV import;
- orders;
- order status update;
- promocodes;
- order export.

## Still honest limitations

v30 is suitable for an internal pilot after configuration, but before full public launch you still need:

1. Real production Alembic migration generated from the final database.
2. Real CDN domain for media.
3. YooKassa production keys and webhook test.
4. Monitoring/Sentry.
5. Human QA on Telegram iOS/Android/Desktop.
6. Legal offer/privacy review.
7. Inventory reconciliation with your real warehouse/ERP if stock lives outside this app.
