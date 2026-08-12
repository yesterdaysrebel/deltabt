# Operating the V1 paper-trading bot

**This bot cannot place a real order.** No order-placement method exists
anywhere in the process, the exchange adapter is GET-only, and the build fails
if that changes (`tests/live/test_no_live_trading.py`). Nothing in this document
should be read as preparation for live trading; that is a separate, explicit
phase which does not exist yet.

---

## Running it

### Locally

```bash
pip install -e '.[live,dev]'
docker compose -f deploy/docker/docker-compose.yml up -d db
DATABASE_URL=postgresql://paper:paper@localhost:5432/paper python -m app
```

The dashboard is on `http://localhost:8000`. `/healthz`, `/readyz` and
`/metrics` are on the same port.

### Kubernetes

`deploy/kubernetes/` is what ArgoCD syncs. One secret is required and it is not
an exchange credential:

```bash
kubectl -n deltabot create secret generic deltabot-db \
  --from-literal=url='postgresql://user:pass@host:5432/deltabot'
kubectl -n deltabot create secret generic deltabot-tunnel \
  --from-literal=token='<cloudflared tunnel token>'
```

Three settings in the manifest are load-bearing:

| Setting | Why |
|---|---|
| `replicas: 1` | Two bots on one paper account duplicate every position |
| `strategy: Recreate` | RollingUpdate briefly runs **two** pods; Recreate does not |
| `livenessProbe: /healthz` | Data freshness, not process liveness — see below |

None of them is the actual guarantee. **The single-instance guarantee is a
PostgreSQL advisory lock** taken at startup. A manual `kubectl scale`, a stuck
terminating pod, or somebody running the bot on their laptop against the
production database all defeat the Kubernetes settings; none of them defeats the
lock. The second process fails `pg_try_advisory_lock` and exits.

---

## Configuration

Every value is an environment variable; there is no config file to forget to
mount.

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | localhost | The only secret in the system |
| `DELTABOT_SYMBOLS` | `BTCUSD,ETHUSD,SOLUSD,XRPUSD` | The corrected research universe |
| `DELTABOT_EQUITY` | `10000` | Simulated starting equity |
| `DELTABOT_RISK_PER_TRADE` | `0.005` | 0.5% |
| `DELTABOT_MIN_RR` | `2.0` | |
| `DELTABOT_MAX_OPEN` | `1` | |
| `DELTABOT_MAX_DAILY_LOSS` | `0.02` | Fraction of start-of-day equity |
| `DELTABOT_MAX_TRADES_PER_DAY` | `6` | |
| `DELTABOT_MAX_CONSEC_LOSSES` | `3` | The revenge-trading brake |
| `DELTABOT_BACKFILL_DAYS` | `7` | Warm-up needs ~12h; 7 days covers a weekend outage |
| `DELTABOT_LOG_LEVEL` | `INFO` | |

**Strategy parameters are not environment variables.** They live in
`app/config/strategy.py` and are hashed into every signal's idempotency key, so
changing one is a code change that shows up in the audit trail rather than a
deploy-time flag that does not.

---

## Health, and the failure it exists to catch

A process can be alive, its socket open, its event loop turning, and no market
data arriving. Every process-level probe calls that healthy. It is **worse than
a crash**, because a crash restarts.

`/healthz` therefore describes the data, not the process. It returns 200 only if
**all five** hold:

```
seconds_since_last_ws_message  < 30
last closed 1m candle          < 90s old
candle gaps in the last 5m     == 0
database writable              (an actual write, not a SELECT)
strategy engine running
```

Otherwise **503**, and the response body names every failing check.

`/readyz` is a different question — *has startup finished* — and gates on the
database, the advisory lock, backfill, indicator warm-up, candle sync,
execution init, and no unresolved recovery condition. A bot mid-backfill is not
broken; it is not ready. Conflating the two makes every restart look like an
outage.

**Warm-up takes about 12 hours of 5m bars** (145 bars: WPR 140 plus Wilder
seeding). It is satisfied from REST backfill in seconds at startup, but
`initialDelaySeconds: 300` on the liveness probe exists so a slow backfill is
never mistaken for a dead bot.

---

## What to expect in normal operation

- **~23 setups/day/symbol** detected at 5m (measured on 40 days of real BTCUSD
  data). With `max_open_positions: 1`, `max_trades_per_day: 6` and cooldowns,
  the great majority are *rejected*, which is the risk engine doing its job.
  Every rejection is persisted with the limit it hit.
- **Delta maintenance roughly monthly**, 60–120 minutes, usually Sunday. The bot
  enters `HALTED`, suspends positions (stops do not trigger during maintenance
  on the exchange either), and **explicitly skips the reopen bar** — one
  measured reopen was a **+0.32% one-minute gap**, which every trend indicator
  in the stack reads as a powerful breakout.
- **Funding settles on UTC boundaries**: 05:30 / 13:30 / 21:30 IST for 8h
  symbols. All storage is UTC; the dashboard renders IST.
- **Reconnects are routine.** They are counted, not alerted on individually.

---

## When something is wrong

| Symptom | Meaning | Action |
|---|---|---|
| `/healthz` 503, `websocket_fresh` failing | Feed silent or stale | Self-heals: the client force-reconnects. Persisting > 5 min means Delta is down |
| `/healthz` 503, `database_writable` failing | Postgres unreachable or read-only | **The bot stops trading.** An unrecorded trade is worse than a missed one |
| `/readyz` 503, `no_unresolved_recovery` | Reconciliation failed | **Do not restart blindly.** See below |
| Pod CrashLoopBackOff at startup | Another instance holds the lock, or recovery failed | Check for a second pod, then the logs |
| `incomplete_5m` climbing | Minutes missing from the feed | Check `deltabot_data_gap_total`; gaps are backfilled but signals are suppressed on incomplete bars |

### Reconciliation failure

The bot refuses to become ready if it finds duplicate open positions in a
symbol, or an open position in a symbol not in the configured universe. This is
deliberate: corrupt state must stop the bot, not be quietly tidied up.

```sql
SELECT symbol, count(*) FROM positions
 WHERE status IN ('OPENING','OPEN','SUSPENDED','CLOSING')
 GROUP BY symbol HAVING count(*) > 1;
```

Resolve by hand — decide which position is real, close the other with
`exit_reason = 'SYSTEM_SAFETY'` — then restart. Do not delete rows; the audit
trail is the product.

---

## Answering "why?" from the database

Every question in the brief is answerable in SQL, without reading a log.

```sql
-- Why did it enter?
SELECT symbol, bar_open, direction, conditions_passed, indicators,
       entry_price, stop_price, target_price, reward_risk, detail
  FROM strategy_signals WHERE outcome = 'APPROVED' ORDER BY bar_open DESC;

-- Why did it NOT enter? (this is the majority of the value)
SELECT bar_open, symbol, outcome, rejection_reason, conditions_failed
  FROM strategy_signals
 WHERE outcome IN ('REJECTED','SUPPRESSED') ORDER BY bar_open DESC LIMIT 50;

-- Why THAT position size?
SELECT s.detail->>'risk_amount' AS risk, s.detail->>'quantity' AS qty,
       s.detail->>'equity' AS equity, s.stop_distance_pct
  FROM strategy_signals s WHERE s.idempotency_key = :key;

-- Which risk limits were breached today?
SELECT occurred_at, symbol, limit_name, limit_value, observed_value, reason
  FROM risk_events WHERE occurred_at > now() - interval '1 day';

-- What configuration was active? (hash, not a version label someone forgot to bump)
SELECT DISTINCT strategy_config_hash, strategy_version, count(*)
  FROM strategy_signals GROUP BY 1, 2;

-- Did a stop or a target really come first? (live fills carry the tick's
-- microsecond exchange timestamp, so ordering is observed, not assumed)
SELECT order_uid, price, liquidity, filled_at, tick_ts_us
  FROM paper_fills ORDER BY filled_at DESC LIMIT 20;
```

---

## The 30-day forward test

**Do not change anything during it.** Not the WPR period, the WPR rule, the ADX
threshold, the Supertrend parameters, the stop logic, or the risk model. If you
want to change one, that is a separately registered experiment with its own
record — and the config hash will make the boundary visible in the data.

What 30 days can establish: the bot fires when the rules say it should, sizes
correctly every time, never breaches a limit, never duplicates a position, and
survives restarts, disconnects and a maintenance window.

What it **cannot** establish: whether this is profitable. H-WPR-1 was classified
**NO ECONOMIC EDGE** in the research program, and a month at this trade rate has
nowhere near the power to overturn or confirm that. Read the forward-test record
as evidence about the *engine*, not about the *edge*.

---

## Backup

The database is the entire product. Everything else is regenerable.

```bash
pg_dump "$DATABASE_URL" --format=custom --file=deltabot-$(date +%F).dump
```

Losing it means losing the forward-test record, which is the one thing 30 days
of running actually produces.
