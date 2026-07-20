# FLASHIN v30 production runbook

## 1. Server

Install:

```bash
apt update
apt install -y docker.io docker-compose-plugin unzip
```

## 2. Upload

```bash
unzip flashin-miniapp-v30.zip
cd flashin-miniapp-v30
cp .env.example .env
python3 scripts/ensure_webhook_secret.py --env-file .env
```

`TELEGRAM_WEBHOOK_SECRET=replace_with_random_webhook_secret` запрещён. Секрет
должен быть сгенерирован до запуска `preflight.py --require-env`; команда выше
не перезаписывает уже установленный реальный секрет. Никогда не публикуйте
секрет и не добавляйте `.env` в Git.

## 3. Configure env

Mandatory:

```env
TELEGRAM_BOT_TOKEN=
BOT_TOKEN=
TELEGRAM_WEBHOOK_SECRET=<generated, do not publish>
JWT_SECRET=
ADMIN_EMAIL=
ADMIN_PASSWORD=
MINI_APP_URL=https://mini.flashin.store
API_PUBLIC_URL=https://api.flashin.store
YOOKASSA_SHOP_ID=
YOOKASSA_SECRET_KEY=
YOOKASSA_RETURN_URL=https://mini.flashin.store/payment-result
```

For R2:

```env
MEDIA_STORAGE=r2
S3_ENDPOINT_URL=
S3_BUCKET=
S3_ACCESS_KEY_ID=
S3_SECRET_ACCESS_KEY=
MEDIA_PUBLIC_BASE_URL=https://cdn.flashin.store
```

## 4. Preflight

```bash
python3 scripts/preflight.py --require-env
```

## 5. Start

```bash
docker compose up -d --build
```

## 6. Logs

```bash
docker compose logs -f backend
docker compose logs -f frontend
docker compose logs -f admin
docker compose logs -f bot
```

## 7. Health

```bash
curl https://api.flashin.store/health
curl https://api.flashin.store/ready
```

## 8. BotFather

```text
/setdomain
mini.flashin.store
```

## 9. YooKassa

Webhook URL:

```text
https://api.flashin.store/api/payments/webhook/yookassa
```

Events:

```text
payment.succeeded
payment.canceled
```

## 10. Admin

Open:

```text
https://admin.flashin.store
```

Login with `ADMIN_EMAIL` / `ADMIN_PASSWORD`.

## 11. Backup

```bash
scripts/backup_postgres.sh
```

Recommended cron:

```cron
0 3 * * * cd /opt/flashin && scripts/backup_postgres.sh
```

## 12. Restore

```bash
scripts/restore_postgres.sh backups/flashin_YYYYMMDD_HHMMSS.sql.gz
```

## 13. Rollback

```bash
docker compose down
git checkout previous_tag
docker compose up -d --build
```

If DB migration was applied, restore from backup.
