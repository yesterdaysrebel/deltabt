#!/usr/bin/env bash
# Follow the bot's logs, optionally filtered by component.
#   ./scripts/logs.sh                  everything, following
#   ./scripts/logs.sh risk             only risk-related lines
#   ./scripts/logs.sh incident         errors, criticals, quarantines, drift
set -uo pipefail
CONTAINER="${DELTABOT_CONTAINER:-deltabot}"
FILTER="${1:-}"
case "$FILTER" in
  "")          docker logs -f --tail 200 "$CONTAINER" ;;
  market)      docker logs -f --tail 500 "$CONTAINER" | grep -Ei 'feed|candle|gap|websocket|backfill|halt' ;;
  strategy)    docker logs -f --tail 500 "$CONTAINER" | grep -Ei 'signal|evaluat|suppress|setup' ;;
  risk)        docker logs -f --tail 500 "$CONTAINER" | grep -Ei 'risk|reject|cooldown|limit|exposure' ;;
  execution)   docker logs -f --tail 500 "$CONTAINER" | grep -Ei 'order|fill|position|resize' ;;
  persistence) docker logs -f --tail 500 "$CONTAINER" | grep -Ei 'database|persist|durable|postgres' ;;
  health)      docker logs -f --tail 500 "$CONTAINER" | grep -Ei 'health|ready|heartbeat|stale' ;;
  recovery)    docker logs -f --tail 500 "$CONTAINER" | grep -Ei 'recover|restart|reconcil|bound to experiment' ;;
  incident)    docker logs --tail 5000 "$CONTAINER" | grep -Ei '"level": "(ERROR|CRITICAL)"|QUARANTIN|DRIFT|RECONCILIATION_FAILED|LOOP_ERROR' ;;
  *)           echo "usage: $0 [market|strategy|risk|execution|persistence|health|recovery|incident]"; exit 2 ;;
esac
