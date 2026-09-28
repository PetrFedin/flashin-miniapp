#!/usr/bin/env bash
set -euo pipefail

key="flashin:rate-limit:persistence-smoke"

docker compose up -d redis
for _ in $(seq 1 60); do
  if docker compose exec -T redis redis-cli ping >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker compose exec -T redis redis-cli ping >/dev/null

appendonly="$(docker compose exec -T redis redis-cli --raw CONFIG GET appendonly | tail -n 1 | tr -d '\r')"
appendfsync="$(docker compose exec -T redis redis-cli --raw CONFIG GET appendfsync | tail -n 1 | tr -d '\r')"
test "$appendonly" = "yes"
test "$appendfsync" = "everysec"

docker compose exec -T redis redis-cli DEL "$key" >/dev/null
docker compose exec -T redis redis-cli --raw EVAL \
  "local t=redis.call('TIME'); local ms=(tonumber(t[1])*1000)+math.floor(tonumber(t[2])/1000); redis.call('ZADD',KEYS[1],ms,'persisted-member'); redis.call('PEXPIRE',KEYS[1],61000); return ms" \
  1 "$key" >/dev/null
sleep 2

docker compose restart redis >/dev/null
for _ in $(seq 1 60); do
  if docker compose exec -T redis redis-cli ping >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
docker compose exec -T redis redis-cli ping >/dev/null

count="$(docker compose exec -T redis redis-cli --raw ZCARD "$key" | tr -d '\r')"
test "$count" = "1"
docker compose exec -T redis redis-cli DEL "$key" >/dev/null

printf '{"status":"ok","aof":"%s","appendfsync":"%s","budget_survived_restart":true}\n' "$appendonly" "$appendfsync"
