# FLASHIN v42 — release operations layer

## Added

### Production deploy script

```bash
scripts/deploy_production.sh
```

Flow:

```text
preflight
docker build
backup
db start
alembic migration
production services up
healthcheck
e2e smoke
```

### Rollback script

```bash
scripts/rollback.sh previous-release.zip backup.sql.gz
```

Flow:

```text
stop services
restore previous release files
optional DB restore
start services
e2e smoke
```

### Backup verification

```bash
scripts/verify_backup.sh backups/flashin_xxx.sql.gz
```

Restores backup into temporary database and lists tables.

### Seed admin

```bash
scripts/seed_admin.py
```

Creates owner admin from:

```env
ADMIN_EMAIL
ADMIN_PASSWORD
```

### API versioning scaffold

```text
/api/v1/version
```

New public clients should move to `/api/v1` over time.

### Secrets manager template

```text
deploy/secrets/infisical.template.env
```

Can be imported into Infisical, 1Password, Vault or another secrets manager.

### Release manifest

```text
deploy/release/release_manifest.template.json
```

Tracks release version, migration, pre/post deploy and rollback plan.

## Why v42 matters

v41 made the product platform mature. v42 makes releases safer:

```text
deploy
backup
migrate
healthcheck
smoke test
rollback
verify backup
seed admin
```
