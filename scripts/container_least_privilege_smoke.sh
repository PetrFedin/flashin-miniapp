#!/usr/bin/env bash
set -euo pipefail

export COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml:docker-compose.production.yml}"
export COMPOSE_PROFILES="${COMPOSE_PROFILES:-production,workers,scheduler,search}"

services=(
  backend
  bot
  notification_worker
  ops_jobs
  outbox_jobs
  provider_command_jobs
  moysklad_sync
  campaign_jobs
  sla_jobs
  event_jobs
  media_jobs
  scheduler
)

probe_runtime='
set -eu
test "$(id -u)" = "10001"
test "$(id -g)" = "10001"
grep -Eq "^NoNewPrivs:[[:space:]]+1$" /proc/self/status
grep -Eq "^CapEff:[[:space:]]+0+$" /proc/self/status
grep -Eq "^CapBnd:[[:space:]]+0+$" /proc/self/status
root_opts="$(awk '"'"'$2 == "/" {print $4; exit}'"'"' /proc/mounts)"
case ",$root_opts," in
  *,ro,*) ;;
  *) echo "root filesystem is not read-only: $root_opts" >&2; exit 71 ;;
esac
if touch /app/.rootfs-write-probe 2>/dev/null; then
  rm -f /app/.rootfs-write-probe
  echo "unexpected writable /app rootfs" >&2
  exit 72
fi
touch /tmp/flashin-runtime-probe
rm -f /tmp/flashin-runtime-probe
'

for service in "${services[@]}"; do
  docker compose run --rm --no-deps -T --entrypoint sh "$service" -c "$probe_runtime"
  printf '{"service":"%s","uid":10001,"gid":10001,"rootfs":"ro","tmp":"rw","caps":"none","no_new_privs":true}\n' "$service"
done

docker compose run --rm --no-deps -T --entrypoint sh backend -c '
set -eu
touch /app/media/.flashin-media-write-probe
rm -f /app/media/.flashin-media-write-probe
touch /app/exports/.flashin-export-write-probe
rm -f /app/exports/.flashin-export-write-probe
'

docker compose run --rm --no-deps -T --entrypoint sh media_jobs -c '
set -eu
touch /app/media/.flashin-media-worker-write-probe
rm -f /app/media/.flashin-media-worker-write-probe
'

printf '{"status":"ok","runtime_services":%d,"backend_media":"rw","backend_exports":"rw","media_worker_media":"rw"}\n' "${#services[@]}"
