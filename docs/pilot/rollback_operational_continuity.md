# Pilot rollback operational continuity

The controlled pilot rollback must recover more than the public API. A rollback is not considered operationally complete if orders can be accepted while durable provider dispatch or the internal alerting plane is absent.

## Continuous production processes

Production deploy explicitly supervises these long-running pilot processes in addition to the core web stack:

- `notification_worker` for Telegram notification delivery;
- `provider_command_jobs` for low-latency durable MoySklad/provider command dispatch;
- `scheduler` as the distributed-lock-protected fallback for reconciliation, outbox, provider commands, SLA, campaign, event and MoySklad jobs.

The other `workers` profile entrypoints are intentionally not all started as permanent daemons. Several are one-shot operator entrypoints for the same distributed-lock job IDs already scheduled by `scheduler`.

## Rollback preflight

Before stopping the running stack, `scripts/rollback.sh` verifies the immutable rollback archive and additionally requires the operational-plane files needed to restore:

- Alertmanager rendering;
- provider-command dispatch;
- Prometheus pilot rules;
- Grafana Prometheus provisioning;
- production Compose definitions.

An older archive without this operational plane is rejected **before downtime**.

## Secret and runtime preservation

Release synchronization uses `rsync --delete`, but it explicitly preserves:

- `deploy/secrets/alertmanager.env` — the root-only external receiver input;
- `deploy/runtime/` — generated local runtime configuration.

Neither location belongs in the immutable application release archive. In production the rolled-back renderer rebuilds `deploy/runtime/alertmanager.yml` from the preserved root-only secret before services are restarted.

## Post-rollback checks

After database restoration and migration/integrity checks, rollback starts and verifies:

- backend readiness;
- Meilisearch health;
- `provider_command_jobs` running;
- Alertmanager readiness;
- Prometheus pilot rule loading and Alertmanager discovery;
- Grafana health;
- core Mini App/Admin/API/bot services;
- notification worker and scheduler;
- container smoke checks.

The release pointer is promoted only after these checks pass. The pilot runtime remains stopped and requires a fresh admission before checkout can resume.

## CI proof

`scripts/release_rollback_smoke.sh` exercises the signed full release rollback with the `monitoring` profile enabled. It proves database restoration, target-image rebuild, release pointer promotion, durable provider worker restoration, Alertmanager/Prometheus/Grafana continuity and signed rollback evidence.

The CI drill uses the checked-in local Alertmanager null receiver; it does **not** claim external on-call delivery. Production deployment still requires the isolated external Alertmanager delivery smoke, and a live pilot must rerun signed deployed readiness/provider evidence before runtime arm.
