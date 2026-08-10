# FLASHIN pilot release and rollback runbook

## Purpose

A controlled pilot may start only when the code that passed protected-main CI is the exact code used to build the deployed containers, can be identified after deployment, and can be rolled back without deleting server state.

For pilot releases there are two separate trust boundaries:

1. GitHub Actions `Release` produces an immutable tracked-files-only ZIP and adjacent `.sha256` for the exact protected, exact-green `main` commit.
2. The production host deploys that retained artifact. It must never silently re-package the host checkout and call the result the release.

## Immutable release artifact

`scripts/release_control.py create` is the packaging primitive used by the guarded GitHub Release workflow. Every ZIP contains `release_manifest.json` with:

- release ID and exact Git commit;
- exact tracked file list;
- SHA-256 and size for every file;
- executable mode for scripts;
- a policy forbidding secrets and symlinks.

The adjacent `.sha256` protects the ZIP itself. Verification rejects duplicate entries, path traversal, absolute paths, symlinks, forbidden files and payload/manifest mismatches.

Production-retained artifacts live under:

- `deploy/release/builds/` — immutable ZIP files and sidecar checksums;
- `deploy/release/runtime/current_release.json` — last successfully deployed release;
- `deploy/release/runtime/previous_release.json` — release available for rollback.

These paths are ignored by Git and must be retained by the production host backup policy.

## Artifact-bound deployment gate

Before **any pilot runtime mutation**, `scripts/deploy_release_gate.py` requires all of the following:

- the requested ZIP is retained under `deploy/release/builds/`;
- the adjacent `.sha256` exists and matches;
- normal immutable release verification succeeds;
- the manifest `git_commit` exactly equals the production checkout `HEAD`;
- the production checkout has no modified tracked files and no non-ignored untracked files;
- the manifest tracked-file set exactly matches the deploy checkout tracked-file set;
- every deploy-checkout tracked file has the same SHA-256 and Git-semantic executable bit as the artifact.

Docker images are then built from a temporary extraction of the **verified ZIP**, not from the host checkout. The host checkout is only the control plane for the production `.env`, release pointers, persistent volumes and operator scripts. The gate is re-run after image build before migrations/runtime operations so control-plane drift fails closed.

`.env` is copied into the temporary extracted directory only so Compose can resolve build arguments and `env_file`; it is mode `0600`, excluded by `.dockerignore`, and the temporary tree is deleted after build.

## Signed pilot release capability v18

For the controlled first-20 pilot, a structurally valid ZIP is not sufficient. Both release pointers must contain a signed `pilot_runtime_guard` capability **v18** bound to the exact release ID, Git commit and archive SHA-256.

Capability v18 additionally proves that the archive contains the artifact-bound deployment gate, its regression tests and an operator deploy path that verifies and extracts the retained release before stopping the pilot runtime or building images.

Inspect and verify:

```bash
python3 scripts/pilot_release_capability.py inspect \
  --archive deploy/release/builds/flashin_<release>.zip
python3 scripts/pilot_release_capability.py verify --slot both --env .env
```

Any older capability, unsigned pointer, mismatched signature/binding, missing deploy provenance guard, missing breaker file or missing safety marker is immediate NO-GO.

## Standard production deployment

### 1. Produce the release in GitHub

Only after `main` is protected and the exact `main` commit has a successful **push** CI across all six required jobs (`backend`, `frontend`, `admin`, `browser-e2e`, `integrated-e2e`, `docker`), run the guarded GitHub `Release` workflow for that exact `main` SHA.

Download its two files to the pilot host without renaming or modifying their bytes:

```text
deploy/release/builds/flashin_<release>.zip
deploy/release/builds/flashin_<release>.zip.sha256
```

Do not use `make release-create` as the production promotion path. That command is useful for deterministic local/CI release tests, but production pilot deployment must consume the retained GitHub Release artifact.

### 2. Verify configuration and artifact

```bash
make validate-env
python3 scripts/provider_wiring_preflight.py --env .env
make readiness-gate
make release-verify FILE=deploy/release/builds/flashin_<release>.zip
python3 scripts/deploy_release_gate.py \
  --archive deploy/release/builds/flashin_<release>.zip
python3 scripts/pilot_release_capability.py inspect \
  --archive deploy/release/builds/flashin_<release>.zip
```

Every command must succeed before deployment.

### 3. Deploy the exact retained artifact

```bash
make deploy-prod RELEASE=deploy/release/builds/flashin_<release>.zip
```

Bare `make deploy-prod` is intentionally rejected.

The deployment sequence is:

1. require the retained ZIP and adjacent checksum;
2. verify artifact/checksum/manifest/commit/file-set/file hashes/executable bits and clean deploy checkout;
3. inspect pilot release capability v18;
4. extract the verified artifact to an isolated temporary Docker build context;
5. only then stop an active pilot checkout runtime;
6. run strict predeploy readiness and render root-only Alertmanager configuration;
7. build production images from the extracted artifact, not the host source tree;
8. re-run the artifact/deploy-checkout binding gate;
9. check a single Alembic head, start PostgreSQL and run transaction-integrity audit;
10. create and verify a pre-migration backup when a schema already exists;
11. apply migrations and verify post-migration transaction/pilot-runtime integrity;
12. prove external Alertmanager delivery and start all production services/workers/search/monitoring;
13. verify backend, Meilisearch, Prometheus, Alertmanager, Grafana and container smokes;
14. promote the retained artifact to `current` only after all checks pass;
15. stamp the promoted release with signed pilot capability v18.

A failed deployment is not promoted. When a pre-migration backup exists the failure output includes its verified path and recovery command.

## Building current and previous rollback slots

A pilot requires two **different** retained capability-v18 releases so rollback is real, not a pointer to the same archive. Deploy the older verified artifact first, then the intended pilot artifact:

```bash
make deploy-prod RELEASE=deploy/release/builds/flashin_<older>.zip
make deploy-prod RELEASE=deploy/release/builds/flashin_<pilot>.zip
make release-status
python3 scripts/pilot_release_capability.py verify --slot both --env .env
```

Do not manually edit release pointers.

## Backup and restore rules

Create and verify a backup:

```bash
make backup
make verify-backup FILE=backups/flashin_YYYYMMDD_HHMMSS.sql.gz
```

A restore is destructive. It is allowed only when application services are stopped. The restore path validates the signed backup manifest and gzip archive, rejects reserved PostgreSQL database names, recreates the application database, restores with `ON_ERROR_STOP`, and requires public tables plus `alembic_version` after restoration.

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

The rollback script verifies release and backup artifacts plus the signed capability before stopping services. It then safely extracts the target release, synchronizes code while protecting `.git`, `.env`, backups, media, exports, logs, data volumes, release archives and private pilot evidence, restores the database, checks migration compatibility and transaction integrity, starts required production services, runs readiness/smoke checks and promotes the restored release.

An explicit retained archive may be used instead of `previous`:

```bash
make rollback \
  RELEASE=deploy/release/builds/flashin_<release>.zip \
  BACKUP=backups/flashin_<timestamp>.sql.gz
```

## Code-only rollback

Code-only rollback is fail-closed because an older application may be incompatible with a newer database schema. It requires an explicit override after schema compatibility review:

```bash
ALLOW_CODE_ONLY_ROLLBACK=1 make rollback RELEASE=previous
```

The override does not bypass release capability inspection.

## Mandatory pilot rollback drill

Before admitting pilot customers:

1. Complete two successful artifact-bound deployments from the capability-v18 code line.
2. Verify both signed pointers with `python3 scripts/pilot_release_capability.py verify --slot both --env .env`.
3. Confirm `make release-status` shows two different retained archive paths.
4. Create and verify a fresh database backup.
5. During a maintenance window, roll back to `previous` with that backup.
6. Confirm all required services are running and `/health` plus `/ready` are semantically healthy.
7. Run `make pilot-gate` after rollback.
8. Redeploy the intended retained pilot artifact with `make deploy-prod RELEASE=...` and run `make pilot-gate` again.
9. Verify capability v18 for both pointers again.
10. Record operator, timestamps, release IDs, archive hashes, backup filename, duration, result and corrective action in private signed pilot evidence.

## Immediate NO-GO conditions

Do not admit pilot users when any of the following is true:

- `main` is not protected with the exact six required GitHub Actions checks;
- the candidate lacks successful protected-main **push** CI and guarded GitHub Release artifact;
- the deployment ZIP or adjacent `.sha256` is missing or not retained under `deploy/release/builds/`;
- the artifact manifest commit differs from production checkout `HEAD`;
- the production checkout contains modified tracked files or non-ignored untracked files;
- tracked file set, hashes or executable bits differ from the retained artifact;
- deploy would build Docker images from the host source tree instead of the verified artifact extraction;
- either current or previous release lacks valid signed `pilot_runtime_guard` capability v18;
- current and previous refer to the same archive;
- `previous_release.json` is missing before a required rollback drill;
- the database backup has not passed restore verification;
- rollback deletes or modifies `.env`, backups, media or runtime evidence;
- the restored database lacks public tables or `alembic_version`;
- any required production service, readiness, migration, provider, monitoring or smoke check fails;
- final lifecycle, repository-governance, P01-P20 admission or first-20 control is not GO.
