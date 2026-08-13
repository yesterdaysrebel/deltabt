#!/usr/bin/env bash
# One screen an operator can read at a glance. Read-only: it changes nothing.
set -uo pipefail
API="${DELTABOT_API:-http://127.0.0.1:8000}"
CONTAINER="${DELTABOT_CONTAINER:-deltabot}"

hr() { printf '%s\n' "------------------------------------------------------------"; }
q()  { python3 -c "import json,sys;d=json.load(sys.stdin);print($1)" 2>/dev/null || echo "?"; }

echo "DELTABOT STATUS   $(date -u '+%Y-%m-%d %H:%M:%S UTC')"; hr

echo "CONTAINER"
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  docker inspect --format '  state       {{.State.Status}} (health: {{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}})
  started     {{.State.StartedAt}}
  restarts    {{.RestartCount}}
  image       {{.Config.Image}}' "$CONTAINER"
else
  echo "  NOT PRESENT -- the bot container does not exist on this host"
fi
hr

S=$(curl -sf --max-time 5 "$API/api/status" 2>/dev/null)
if [ -z "$S" ]; then
  echo "BOT        UNREACHABLE at $API"
  echo "           (if the container is up, it may still be warming up)"
  hr
else
  echo "BOT"
  echo "$S" | q "'  healthy     %s' % d['healthy']"
  echo "$S" | q "'  ready       %s' % d['ready']"
  echo "$S" | q "'  failing     %s' % (', '.join(d['failing_checks']) or 'none')"
  echo "$S" | q "'  uptime      %.1f h' % (d['uptime_seconds']/3600)"
  echo "$S" | q "'  strategy    %s' % d['strategy_version']"
  echo "$S" | q "'  config hash %s' % d['strategy_config_hash']"
  hr
  echo "MARKET DATA"
  echo "$S" | q "'  ws          %s, last msg %.1fs ago' % ('connected' if d['ws_connected'] else 'DISCONNECTED', d['seconds_since_ws_message'] or -1)"
  echo "$S" | q "'  last 1m     %s' % (d['last_closed_1m_ist'] or '-')"
  echo "$S" | q "'  reconnects  %d   stale events %d' % (d['feed']['websocket_reconnects'], d['feed']['stale_feed_events'])"
  echo "$S" | q "'  gaps        %d recent' % d['recent_gaps']"
  echo "$S" | q "'  candles     1m=%d 5m=%d  (incomplete 5m: %d)' % (d['metrics']['candles_1m'], d['metrics']['candles_5m'], d['metrics']['incomplete_5m'])"
  hr
  echo "STRATEGY / EXECUTION"
  echo "$S" | q "'  signals     detected %d, rejected %d, duplicates %d' % (d['metrics']['signals_detected'], d['metrics']['signals_rejected'], d['metrics']['duplicate_signals'])"
  echo "$S" | q "'  orders      %d entry, %d exit, %d expired, %d refused (exposure)' % (d['metrics']['orders'], d['metrics']['exit_orders'], d['metrics']['orders_expired'], d['metrics']['reservations_refused'])"
  echo "$S" | q "'  fills       %d   quarantined %d' % (d['metrics']['fills'], d['metrics']['fills_quarantined'])"
  echo "$S" | q "'  funding     %d settlements charged' % d['metrics']['funding_events']"
  hr
  R=$(curl -sf --max-time 5 "$API/api/risk" 2>/dev/null)
  echo "RISK"
  echo "$R" | q "'  equity      \$%.2f  (peak \$%.2f)' % (d['equity'], d['peak_equity'])"
  echo "$R" | q "'  daily P&L   \$%+.2f   remaining \$%.2f' % (d['daily_pnl'], d['daily_loss_remaining'])"
  echo "$R" | q "'  drawdown    %.2f%%' % d['drawdown_pct']"
  echo "$R" | q "'  trades      %d/%d today' % (d['trades_today'], d['max_trades_per_day'])"
  echo "$R" | q "'  losses      %d/%d consecutive' % (d['consecutive_losses'], d['max_consecutive_losses'])"
  echo "$R" | q "'  cooldown    trade %ds, loss %ds' % (d['cooldown_trade_remaining'], d['cooldown_loss_remaining'])"
  hr
  echo "POSITIONS"
  curl -sf --max-time 5 "$API/api/positions" 2>/dev/null | python3 -c "
import json,sys
rows=json.load(sys.stdin)
if not rows: print('  none open')
for r in rows:
    print('  %-8s %-5s qty=%-6d entry=%-10.2f stop=%-10.2f R=%+.2f  since %s'
          % (r['symbol'],r['side'],r['quantity'],r['entry'],r['stop'],r['r'],r['opened_ist']))" 2>/dev/null || echo "  ?"
  hr
fi

echo "EXPERIMENT"
if [ -n "${DATABASE_URL:-}" ]; then
  PYTHONPATH="${DELTABOT_ROOT:-/opt/deltabt}" python3 -m app forward-test status 2>/dev/null \
    | grep -E "experiment_id|status|git_sha|config_hash|risk_hash|started_at" | sed 's/^/  /' \
    || echo "  (status command unavailable)"
else
  echo "  DATABASE_URL not set in this shell -- experiment identity unavailable"
fi
hr
echo "PAPER TRADING ONLY. This bot has no order-placement capability."
