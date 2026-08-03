# FLASHIN pilot release and rollback runbook

## Purpose

A pilot may start only when the deployed code can be identified, verified and rolled back without deleting server state. This runbook covers the immutable release archive, pre-migration backup, deployment promotion and rollback drill.

## What the release archive guarantees

`scripts/release_control.py create` packages only clean, Git-tracked regular files. It excludes secrets, local databases, backups, media, exports, runtime reports and release-state files.

Every ZIP contains `release_manifest.json` with:

- release ID and Git commit;
- exact file list;
- SHA-256 and size for every file;
- executable mode for scripts;
- a policy stating that secrets and symlinks are forbidden.

A sidecar `.sha256` file protects the ZIP itself. Verification rejects duplicate entries, path traversal, absolute paths, symlinks, forbidden files and any payload that differs from the manifest.

Local artifacts are stored under:

- `deploy/release/builds/` — immutable ZIP files and checksums;
- `deploy/release/runtime/current_release.json` — last successfully deployed release;
- `deploy/release/runtime/previous_release.json` — release available for rollback.

These paths are deliberately ignored by Git and must be retained by the production host backup policy.

## Standard production deployment

Run from a clean production checkout:

```bash
make readiness-gate
make deploy-prod
```

`deploy-prod` performs the following sequence:

1. strict predeploy readiness gate;
2. immutable release creation and verification;
3. image build and single-Alembic-head check;
4. database startup and transaction-integrity audit;
5. pre-migration PostgreSQL backup for an existing schema;
6. backup restore verification;
7. migration and post-migration integrity audit;
8. startup of all production services;
9. backend, Meilisearch and container smoke checks;
10. promotion of the release to `current` only after every check passes.

A failed deployment is not promoted. The console prints the verified backup path and the rollback command when a pre-migration backup exists.

## Backup and restore rules

Create and verify a backup:

```bash
make backup
make verify-backup FILE=backups/flashin_YYYYMMDD_HHMMSS.sql.gz
```

A restore is destructive. It is allowed only when application services are stopped. The restore script:

1. validates the gzip archive;
2. rejects reserved PostgreSQL database names;
3. drops and recreates the configured application database;
4. restores with `ON_ERROR_STOP`;
5. requires public tables and `alembic_version` after restoration.

Interactive restore:

```bash
make restore FILE=backups/flashin_YYYYMMDD_HHMMSS.sql.gz
```

Non-interactive restore is reserved for the controlled rollback script:

```bash
scripts/restore_postgres.sh --yes backups/flashin_YYYYMMDD_HHMMSS.sql.gz
```

## Rollback with database restoration

Preferred rollback path:

```bash
make rollback RELEASE=previous BACKUP=backups/flashin_YYYYMMDD_HHMMSS.sql.gz
```

The rollback script verifies both artifacts before stopping services. It then safely extracts the target release, synchronizes code while protecting `.git`, `.env`, backups, media, exports, logs, data volumes, release archives and private pilot evidence, restores the database, checks migration compatibility and transaction integrity, starts every production service, runs readiness and smoke checks, and promotes the restored release.

An explicit archive may be used instead of `previous`:

```bash
make rollback RELEASE=deploy/release/builds/flashin_<release>.zip BACKUP=backups/flashin_<timestamp>.sql.gz
```

## Code-only rollback

Code-only rollback is fail-closed because an older application may be incompatible with a newer database schema. It requires an explicit override after schema compatibility has been reviewed:

```bash
ALLOW_CODE_ONLY_ROLLBACK=1 make rollback RELEASE=previous
```

Do not use this override when the failed release applied destructive or non-backward-compatible migrations.

## Mandatory pilot rollback drill

Before admitting pilot customers:

1. Complete at least two successful deployments so both `current` and `previous` release pointers exist.
2. Confirm `make release-status` shows valid archive paths retained on the production host.
3. Create and verify a fresh database backup.
4. During a maintenance window, run rollback to `previous` with that backup.
5. Confirm all required services are running.
6. Confirm `/health` and `/ready` return successful semantic responses.
7. Run `make pilot-gate` after rollback.
8. Redeploy the intended pilot release and run `make pilot-gate` again.
9. Record the drill date, operator, release IDs, backup filename, duration, result and any corrective action in the private pilot evidence log.

## Immediate NO-GO conditions

Do not admit pilot users when any of the following is true:

- no verified release archive exists for the deployed commit;
- the production checkout contains uncommitted tracked changes;
- `previous_release.json` is missing before a required rollback drill;
- the database backup has not passed restore verification;
- rollback deletes or modifies `.env`, backups, media or runtime evidence;
- the restored database lacks public tables or `alembic_version`;
- any required production service, readiness check, migration check or smoke test fails;
- the final live provider gate or 20-order pilot control is not GO.
