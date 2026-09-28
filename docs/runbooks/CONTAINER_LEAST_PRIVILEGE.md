# Container least-privilege authority

## Purpose

FLASHIN production application containers must not rely on root privileges or a writable root filesystem.

This contract applies to the FastAPI backend, Telegram bot, notification worker and every Python application worker built from `Dockerfile.backend` or `Dockerfile.bot`.

## Runtime identity

- UID/GID: `10001:10001`.
- Linux capabilities: all dropped.
- `no-new-privileges`: required.
- root filesystem: read-only.
- Python bytecode writes: disabled.
- runtime temp/cache home: `/tmp`, mounted as tmpfs.

Production Compose must not override the image back to root.

## Writable paths

Writable application paths are deliberately narrow:

- backend: `/app/media`, `/app/exports`, `/tmp`;
- media worker: `/app/media`, `/tmp`;
- bot and all other workers: `/tmp` only.

`/app/docs` and `/app/deploy/release` remain read-only evidence mounts.

The production default uses R2 object storage, but the media volume remains an explicit compatibility boundary for controlled local-storage operation.

## Fail-closed validation

`scripts/check_production_compose.py` rejects a resolved production graph when an application runtime:

- runs as another UID/GID;
- has a writable root filesystem;
- retains Linux capabilities;
- lacks `no-new-privileges`;
- lacks writable `/tmp` tmpfs;
- exposes an unexpected writable mount below `/app`;
- loses a required media/export named volume.

Do not bypass the validator to recover a deployment.

## Runtime proof

`scripts/container_least_privilege_smoke.sh` runs every application service from the resolved production Compose graph and proves:

- effective UID/GID is 10001;
- `NoNewPrivs` is set;
- effective and bounding capability sets are zero;
- the root mount is read-only;
- arbitrary `/app` writes fail;
- `/tmp` works;
- only the explicitly allowed media/export mounts are writable.

The smoke runs in the mandatory Docker CI gate after images are built.

## Operator response

If the container smoke or production Compose validator fails:

1. do not promote the release;
2. identify whether the failure is identity, capabilities, rootfs or writable-path drift;
3. keep the narrow writable-path model; do not make the root filesystem writable as a workaround;
4. fix the image or Compose contract;
5. rerun full CI, integrated E2E, Docker backup/rollback and Security on the exact new head.
