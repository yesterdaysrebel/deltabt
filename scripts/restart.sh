#!/usr/bin/env bash
# Restart the bot cleanly. Does NOT touch the experiment, the database, or any
# open paper position -- state is recovered from PostgreSQL on the way back up.
set -euo pipefail
CONTAINER="${DELTABOT_CONTAINER:-deltabot}"
COMPOSE="${DELTABOT_COMPOSE:-deploy/vps/docker-compose.yml}"

echo "state BEFORE restart:"
curl -sf --max-time 5 "${DELTABOT_API:-http://127.0.0.1:8000}/api/positions" 2>/dev/null \
  | python3 -c "import json,sys;print('  open positions:',len(json.load(sys.stdin)))" 2>/dev/null \
  || echo "  (api unreachable)"

echo "stopping (SIGTERM, up to 45s for a clean shutdown)..."
docker compose -f "$COMPOSE" stop -t 45 bot
echo "starting..."
docker compose -f "$COMPOSE" up -d bot

echo "waiting for readiness..."
for _ in $(seq 1 120); do
  curl -sf --max-time 3 "${DELTABOT_API:-http://127.0.0.1:8000}/readyz" >/dev/null 2>&1 && break
  sleep 5
done
if curl -sf --max-time 3 "${DELTABOT_API:-http://127.0.0.1:8000}/readyz" >/dev/null 2>&1; then
  echo "READY. Open paper positions are preserved -- shutdown never fabricates exits."
else
  echo "NOT READY after 10 minutes. Check: ./scripts/logs.sh recovery"
  exit 1
fi
