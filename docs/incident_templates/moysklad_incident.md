# Incident: MoySklad Sync

## Symptoms

- Products do not update.
- Stock is wrong.
- Sync logs show failed.
- Conflicts appear.

## Immediate actions

1. Stop scheduled sync if wrong data is spreading.
2. Check `moysklad_sync_logs`.
3. Check `moysklad_conflicts`.
4. Verify token and API access.
5. Do not run bulk stock apply until mapping is fixed.

## Recovery

```bash
docker compose --profile workers run --rm moysklad_sync
```

Then inspect:

- SKU;
- size;
- color;
- category;
- price;
- stock.

## Customer risk

Overselling. If risk is high, disable checkout temporarily or set affected products inactive.
