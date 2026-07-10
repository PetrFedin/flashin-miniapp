# Incident: PostgreSQL

## Symptoms

- API `/ready` fails.
- Orders cannot be created.
- Admin cannot load data.

## Immediate actions

1. Stop non-critical workers.
2. Check DB container:
   ```bash
   docker compose logs -f db
   ```
3. Check disk space.
4. Check latest backup.

## Recovery

Restore:

```bash
scripts/restore_postgres.sh backups/latest.sql.gz
```

Verify:

```bash
make health
python tests/e2e_smoke.py
```
