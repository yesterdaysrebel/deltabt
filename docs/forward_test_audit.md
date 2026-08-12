# Pre-forward-test audit

**Audited at commit `a23d59d`. 960 tests passing. No code changed for this audit.**

The question this audit answers is not "does the bot work" — the test suite
already says it does. It is: **would a 30-day forward test starting today
produce a dataset anyone could trust?**

The answer is no, not yet. Eight findings would invalidate the record, and none
of them is in the strategy. Every one is in the plumbing that turns what the bot
did into evidence of what the bot did.

Findings were established by running probes against the real code, real
PostgreSQL and real cached market data, not by reading. Each is reproducible.

---

## Verdict by subsystem

| Subsystem | Verdict | Note |
|---|---|---|
| Safety boundary (no live trading) | **PASS** | 483 AST assertions, negative-controlled against a deliberate violation |
| Advisory lock / single instance | **PASS** | Including release on `kill -9` |
| Duplicate protection | **PASS** | Signals, orders, fills, positions — all by database constraint |
| Closed-bar enforcement / look-ahead | **PASS** | Window invariance + leg-truncation guard + same-bar regression |
| Candle builder | **PASS** | Immutability, gap detection, dedup, OHLC validation |
| Halt handling | **PASS** | Three-state, reopen bar skipped, restart-inside-halt primed |
| Risk engine | **PASS** | 20 ordered gates, each with a test that trips it |
| Restart recovery | **PASS** | All five crash moments |
| Health / readiness | **PASS** | Data freshness, not process liveness |
| **Audit trail — fill records** | **FAIL** | Wrong `side` recorded |
| **Audit trail — JSONB** | **FAIL** | Returns as `str` from PostgreSQL |
| **Audit trail — exit fills** | **FAIL** | Cannot be persisted at all |
| **Funding accounting** | **NOT IMPLEMENTED** | Phase 4 |
| **Forward-test config lock** | **NOT IMPLEMENTED** | Phase 7 |
| **Preflight gate** | **NOT IMPLEMENTED** | Phase 8 |
| **Daily / final reports** | **NOT IMPLEMENTED** | Phases 10–11 |
| **Decision clock** | **FAIL** | Risk timing uses wall clock, not bar time — decisions are not reproducible |
| Observability coverage | **WARNING** | 22 of 35 required metrics absent |
| Startup JIT stall | **WARNING** | 3.0 s of blocked event loop, every cold start |
| Evaluation path cost | **WARNING** | O(total history); dashboard polling competes with the feed |
| Orphaned orders | **WARNING** | Left `WORKING` in the database forever |
| Fill persistence lag | **WARNING** | Up to 1 s behind the tick |
| No time exit | **WARNING** | Correct per frozen research — but see §W5 |

---

# FAIL — must be fixed before starting

## F1. Fill records carry the wrong side

`TradingBot._persist_fill` resolves the position for a fill by scanning for the
first one matching the symbol:

```python
pos = next((x for x in self.broker.positions.values()
            if x.symbol == ev.symbol), None)
```

`broker.positions` never removes closed positions, so from the **second trade in
a symbol onward** this matches a *previously closed* position and takes its
`side` and `quantity`.

Probe result — a long that closed at target, then a short:

```
persisted entry FILL records:
   side=+1 qty=  97 price=63012.6
   side=+1 qty=  97 price=64487.1      <-- this was a SHORT
reality: side=-1 qty=97
CORRECT: False
```

Over 30 days every fill after the first in each symbol is suspect. The dataset
is the deliverable, so this alone disqualifies the run.

## F2. JSONB round-trips as a string

`asyncpg` returns `jsonb` as `str` unless a codec is registered. None is.

```
conditions_failed    type=str    value=["primary_adx_ge_min", "primary_wpr_rising"]
indicators           type=str    value={"adx": 31.2}
```

So `conditions_passed`, `conditions_failed`, `indicators` and `detail` — the
entire "why did it do that" payload — come back unusable. The dashboard's
`(x.conditions_failed || []).slice(0,2)` silently produces nothing.

**Why no test caught it:** the in-memory repository returns real lists, and the
API tests run against that. The shared-scenario tests do run against PostgreSQL,
but they assert on `outcome` and `rejection_reason`, both plain text. This is a
gap in the test design, not just the code.

## F3. Exit fills cannot be persisted

`PaperBroker._close` constructs a `PaperFill` with `order_uid=new_uid("exit")` —
an identifier with no corresponding row in `paper_orders`. The schema has
`REFERENCES paper_orders(order_uid)`:

```
FAILS: ForeignKeyViolationError: insert or update on table "paper_fills"
       violates foreign key constraint "paper_fills_order_uid_fkey"
```

In practice it never fires, because `drain_broker_events` only persists
`kind == "FILL"` and `_close` emits `POSITION_CLOSED`. So **exit fills are
simply never written**. The exit price and reason survive on the `positions`
row, but the exit fee, liquidity (maker vs taker) and the tick timestamp that
proves stop-vs-target ordering do not.

Phase 3 requires both sides of every trade at fill granularity.

## F4. Funding is not applied

Known and previously documented, restated because Phase 4 makes it blocking. The
`positions.funding` column exists, `deltabt.costs` models snapshot funding and
per-symbol intervals, and `FUNDING:<SYMBOL>` history is available — but nothing
in the live loop charges it. Any position held across a settlement understates
cost.

## F5. No forward-test configuration lock

Nothing records a commit SHA. Nothing prevents configuration drift. Two specific
holes:

- **Risk configuration is not hashed.** `strategy_config_hash` covers strategy
  parameters only. `DELTABOT_RISK_PER_TRADE`, `DELTABOT_MIN_RR`,
  `DELTABOT_MAX_TRADES_PER_DAY` and the rest are environment variables read
  fresh on every start. Changing one mid-test is invisible in the data.
- **Nothing refuses to start on a change.** Phase 7 requires the bot to decline
  the next session if the frozen configuration has moved.

## F6. No preflight gate

Phase 8's `forward-test preflight` / `forward-test start` do not exist. There is
no CLI at all for the bot — `python -m app` starts it unconditionally.

## F7. No reports

Phases 10 and 11. No daily report, no final report, and no CronJob or equivalent
in `deploy/`.

## F8. Every timing decision uses the wall clock, so the run is not reproducible

This one was found by accident and is the most consequential.

Cooldowns, the daily-counter rollover and order expiry all compare against
`time.time()`, while every bar, tick and signal carries an **exchange**
timestamp:

```python
decision = self.risk.evaluate(..., now=int(time.time()))      # wall clock
order = self.broker.submit_order(intent, now=int(time.time())) # wall clock
# ...but expiry compares created_at against tick.ts, which is exchange time
```

Two consequences, and the second is the serious one.

**It mixes clock domains inside a single comparison.** `order.created_at` is
wall clock; `tick.ts` is exchange time. More than 90 s of container skew makes
every order expire immediately, or never. Neither fails loudly.

**The forward test cannot be verified by replaying its own record.** Whether a
signal was rejected for cooldown depends on when the *process* saw it, not on
when the *market* produced it. Re-running the recorded bars gives different
decisions. For an experiment whose entire output is an auditable dataset, that
removes the ability to check the dataset against itself.

It also invalidated this audit's own first attempt at measuring sample size:

```
7-day replay, wall clock:   1 trade, 634 rejections, 630 of them "cooldown after trade"
```

630 cooldown rejections cannot follow from one 15-minute cooldown. The replay
compressed 7 days of bars into ~10 minutes of wall clock, so a single trade held
the cooldown open for the entire remaining run. Live, wall clock and exchange
time coincide and the bug is invisible — which is precisely why it survived 960
tests.

**Fix direction:** take `now` from the bar/tick being processed, not from the
system clock, and use the wall clock only for staleness detection (where it is
the right answer, because staleness is about *our* liveness).

---

# WARNING — should be fixed, will not silently corrupt the record

## W1. First evaluation blocks the event loop for 3 seconds

Measured with a cold numba cache:

```
COLD:  import 0.23s | FIRST evaluate 3.03s | second 1.3ms
WARM:  import 0.18s | FIRST evaluate 0.31s | second 1.5ms
```

`warm_up()` counts bars but never calls the indicators, so compilation happens
on the **first live 5 m close**, after the bot has declared itself ready. The
container sets `NUMBA_CACHE_DIR=/tmp/numba` on an `emptyDir`, so every pod
restart is a cold start.

Three seconds is inside the 30 s stale-feed threshold, so it will not trip
health — it will silently delay fills and queue ticks.

## W2. The evaluation path scales with total history, not the window

```
 history    frame()   frame_5m()   evaluate   TOTAL
   2,000       3.0ms       13.0ms      1.8ms    17.8ms
  20,000       3.0ms       52.4ms      2.1ms    57.5ms
```

`frame_5m()` resamples the **entire** ring buffer and then filters to the
window. At the default 20,000-bar buffer that is 52 ms per call.

Live signal evaluation is 4 calls/minute, which is fine. The problem is the
readers: `/readyz` calls `frame_5m()` per symbol on **every probe** (10 s) and
`/api/market` on **every dashboard refresh** (5 s) — roughly 200 ms of
event-loop work per poll, competing with the feed. A dashboard left open should
not be able to affect data capture.

It also makes replay-based verification impractical: a full 30-day replay would
take about 2.9 hours.

## W3. Orphaned orders

A crash between `create_order` and the fill leaves a `WORKING` row in
`paper_orders` permanently — recovery loads open *positions* only. Harmless to
trading, but it pollutes fill-rate and expiry-rate statistics, which Phase 11
reports.

## W4. Fill persistence lags the tick by up to 1 second

`_on_tick` is synchronous and only queues broker events; `drain_broker_events`
runs from the 1 s bar loop. A crash inside that window loses the position
entirely, while its signal and order are already durable — so the record shows
an order that never filled, when in fact it did.

The feed already supports async handlers, so this window can be closed.

## W5. No time exit — correct, but it needs watching

The frozen H-WPR-1 specification has `MAX_HOLD_BARS = 0`, so exits are stop or
target only. V1 matches it. **This must not change** — adding a time exit is a
strategy modification.

But with `max_open_positions = 1` *globally across four symbols*, a single
position that reaches neither barrier blocks the entire experiment for as long
as it stays open. That is a real threat to the 30-day sample and it must be
monitored rather than fixed. Hold-time distribution is measured in §Sample size
below.

## W6. Smaller items

- `heartbeat.last_closed_5m` is always `NULL` — the field is passed as `None`.
- The dashboard reads `state.trades_today` directly, but the UTC day only rolls
  inside `risk.evaluate`, so a day with no evaluations displays stale counters.
- `_repair_gaps` iterates every recorded gap on every closed bar; unbounded over
  30 days.

---

# Observability coverage against Phase 6

22 of 35 required metrics are absent.

| Group | Missing |
|---|---|
| SYSTEM (5/8) | `last_closed_1m`, `last_closed_5m`, `db_status`, `advisory_lock_status`, `maintenance_status` |
| SIGNALS (3/5) | `rejection_reason` breakdown, by symbol, by direction |
| EXECUTION (2/5) | `simulated_slippage`, `average_fill_delay` |
| RISK (4/7) | `risk_per_trade`, `maximum_risk`, `consecutive_losses`, `current_exposure` |
| P&L (8/10) | `fees`, `funding`, `slippage`, `r_multiple`, `win_rate`, `profit_factor`, `average_winner`, `average_loser` |

Most exist somewhere in the process; they are simply not exported. Fill delay
and slippage are not measured at all.

---

# Schema gaps against Phase 3

Required per-order and per-trade fields that have no column:

```
paper_orders  missing: reference_price, fill_price, expires_at, reject_reason,
                       strategy_config_hash, bar_open, fill_delay
positions     missing: planned_r, fill_rr (realised R at fill), slippage,
                       strategy_config_hash
tables        missing: funding_events, forward_test (config snapshot)
```

`fill_rr` is computed by the broker and held in memory but never written.

---

# What is NOT wrong

Recording this so the fix list is not mistaken for a general lack of confidence.
Every one of these was probed or is covered by a test that fails when broken:

- No order-placement path, no credentials, no signing, no live flag. The AST
  scan fails the build on any of them, and it was verified to fail against a
  deliberate violation.
- Two bot processes cannot both trade, including after `kill -9`.
- Duplicate signals, orders, fills and positions are impossible at the database
  level, not merely guarded in application code.
- Signals come only from closed bars; future bars cannot alter past signals; the
  leg-truncation guard prevents a window-dependent stop.
- Maintenance halts suspend rather than close, the reopen bar is skipped, and a
  restart inside a halt comes up halted.
- Realised risk cannot exceed budget: the fill resizes.
- Reward/risk degradation from entry slippage is bounded and recorded.

---

# Sample size — the thing most likely to make 30 days uninformative

Measured on a 7-day, four-symbol replay of real cached data through the full bot
path (`docs/forward_test_audit.md` §appendix for the script).

**First attempt, wall clock (INVALID — see F8):** 1 trade in 7 days, 634
rejections, 630 of them "cooldown after trade". A single 15-minute cooldown
cannot produce 630 rejections; the replay compressed 7 days of bars into ~10
minutes of wall clock, so one trade held the cooldown open for the whole run.
This is what exposed F8.

**Second attempt, clock pinned to bar time: in progress at the time of writing.**
The figure it produces answers "is 30 days long enough to say anything about
execution quality", and will be appended here before the forward test starts.

The concern is arithmetic, not pessimism: `max_open_positions = 1` is global, so
four symbols compete for one slot; `cooldown_after_trade` is 15 minutes;
`cooldown_after_loss` is 60 minutes; and `max_consecutive_losses = 3` stops
trading for the rest of the day on the third loss. Each is correct discipline.
Together they can throttle a ~23-setups/day/symbol detection rate down to very
few trades.

If 30 days yields fewer than ~30 closed trades, the run still validates the
*engine* — which is its stated purpose — but it cannot say anything at all about
execution quality statistics, and the final report must say so explicitly rather
than presenting ratios computed on a handful of trades.


---

# Proposed fix plan

Ordered by whether it blocks the start. **No item touches the strategy.** The
frozen rules, ADX 28, WPR 140, 5 m primary / 1 m confirmation, the structural
stop and the 2R target are untouched by every item below.

### Blocking — the record is wrong or unverifiable without these

| # | Fix | Touches |
|---|---|---|
| F8 | Take `now` from the bar/tick under processing, not `time.time()`. Wall clock retained only for staleness, where it is the correct answer | `bot.py`, `risk/engine.py` signature already accepts `now` |
| F1 | Resolve the position for a fill by `order_uid`, not by scanning for a symbol match | `bot.py::_persist_fill` |
| F2 | Register an `asyncpg` JSONB codec so JSON columns come back as objects; extend the shared PostgreSQL scenarios to assert on them | `repository.py`, `test_persistence.py` |
| F3 | Give exits a real order row (`purpose='stop'` / `'target'`) so the fill has something to reference, and persist both sides | `schema.sql`, `paper_broker.py`, `bot.py` |
| F4 | Funding ledger: settlement detection, per-position cash flow, `funding_events` table, inclusion in realised P&L | new `app/portfolio/funding.py`, `schema.sql`, `bot.py` |
| F5 | `forward_test` snapshot table: strategy + risk + execution config, symbol universe, commit SHA, combined hash, start time. Refuse to start if the hash moved | new `app/forwardtest/` |
| F6 | `preflight` and `start` commands with the full check list | new `app/cli.py` |
| F7 | Daily and final report generators, plus a CronJob | new `app/reports/`, `deploy/` |

### Non-blocking — should still be done first

| # | Fix | Why now |
|---|---|---|
| W1 | Compile the numba kernels during warm-up, before READY | 3 s of blocked loop on every cold start otherwise |
| W2 | Cache the derived 5 m frame per symbol, invalidated on close | stops the dashboard competing with the feed |
| W3 | Reconcile orphaned `WORKING` orders to `EXPIRED` at startup | otherwise fill-rate and expiry-rate stats are wrong |
| W4 | Drain broker events immediately after a tick produces them | narrows the fill-loss window from 1 s to ~0 |
| W6 | Populate `heartbeat.last_closed_5m`; roll the day on a timer as well as inside `evaluate` | small correctness items |
| — | Schema: `planned_r`, `fill_rr`, `slippage`, `reference_price`, `fill_price`, `expires_at`, `reject_reason`, `bar_open`, `strategy_config_hash` | Phase 3 requires them |
| — | Export the 22 missing metrics; measure fill delay and slippage | Phase 6 |

### Explicitly NOT proposed

- No change to any indicator parameter, entry rule, exit rule, stop methodology
  or target multiple.
- No time exit, despite §W5. Adding one would be a strategy change; the risk is
  monitored instead.
- `max_open_positions` stays at 1 even though it is the main throttle on sample
  size. Raising it is a risk-configuration change and belongs to you, not to a
  fix list.
- No re-tuning of `min_fill_rr` or `max_entry_deviation` to admit more trades.
