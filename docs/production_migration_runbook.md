# Production Alembic migration runbook

v32 includes a real production initial migration:

```text
backend/alembic/versions/0001_initial_production.py
```

## Fresh production database

Use:

```bash
cd backend
alembic -c alembic.ini upgrade head
```

Then start backend with:

```env
USE_CREATE_ALL=false
ENABLE_SEED=false
```

## Existing development database

If you already ran previous versions with `Base.metadata.create_all`, do **not** blindly run this migration on the same database.

Recommended:

1. Backup database.
2. Create a new clean database.
3. Run `alembic upgrade head`.
4. Import products through CSV/admin.
5. Run test orders.

## Production rule

In production:

```env
USE_CREATE_ALL=false
```

`Base.metadata.create_all` is acceptable only for local MVP/dev.
