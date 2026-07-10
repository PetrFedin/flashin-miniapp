# MoySklad integration runbook

## 1. Create API token

In MoySklad account create an API token or prepare login/password.

Recommended:

```env
MOYSKLAD_TOKEN=...
```

Fallback:

```env
MOYSKLAD_LOGIN=...
MOYSKLAD_PASSWORD=...
```

## 2. Configure env

```env
MOYSKLAD_BASE_URL=https://api.moysklad.ru/api/remap/1.2
MOYSKLAD_DEFAULT_CURRENCY=RUB
MOYSKLAD_SYNC_LIMIT=100
```

## 3. Run manual sync

From admin panel:

```text
МойСклад / CRM / BI -> Синхронизировать МойСклад
```

Or CLI:

```bash
python scripts/run_moysklad_sync.py
```

Or Docker:

```bash
docker compose --profile workers run --rm moysklad_sync
```

## 4. Check logs

Admin panel:

```text
МойСклад sync logs
```

API:

```text
GET /api/moysklad/sync-logs
```

## 5. Validate mapping

Check:

- SKU;
- title;
- price;
- category;
- stock;
- size;
- product visibility.

## 6. Schedule

Recommended:

```cron
*/30 * * * * cd /opt/flashin && docker compose --profile workers run --rm moysklad_sync
```

## 7. Important

Do not run public launch until you validate how MoySklad stores:

- product variants;
- size;
- color;
- stock;
- price type;
- article/code fields.
