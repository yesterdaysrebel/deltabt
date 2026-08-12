# V1 Paper-Trading Assistant — Phase 1 Architecture Assessment

**Status: proposal awaiting approval. No V1 code has been written.**

This document is the Phase 1 deliverable: an inspection of what exists, what is
reusable, what is missing, and a proposed architecture for the live paper-trading
system. Sections 1–6 are findings about the current repository. Sections 7–12 are
proposals.

The guiding constraint, restated so it governs every decision below: **V1 exists to
enforce execution discipline, not to discover edge.** The research program
(`out/experiments.jsonl`, nine records) classified every hypothesis tested as NO
EDGE, NO SIGNAL, or NO ECONOMIC EDGE, including this exact indicator family
(H-WPR-1: NO ECONOMIC EDGE). V1 treats the strategy as a **user-defined setup
detector** whose correctness is judged by whether it fires when the rules say it
should, not by whether it makes money.

---

## 1. Current architecture

The repository is a **batch research backtester**. It is offline, single-process,
stateless, and array-at-once. There is no live component of any kind.

```
deltabt/
  config.py       paths, verified exchange constants, StrategyParams/WprLatch dataclasses
  costs.py        SymbolCosts — per-symbol fees x GST, tick/contract rounding, funding grid
  indicators.py   numba, Pine-exact: rma, true_range, atr, wpr, dmi, supertrend
  wpr_latch.py    stateful band-traverse FSM (njit scan + step_state + vectorised path)
  strategy.py     build_signals() -> Signals: whole-history boolean arrays
  engine.py       run_backtest(): python bar loop, one position at a time
  metrics.py      Metrics, bootstrap CIs, max drawdown
  runner.py       load_symbol / backtest_symbol / screen_universe
  sweep.py        parameter grid + walk-forward
  cli.py          screen / backtest / sweep / walkforward / wpr-curve
  data/
    client.py     read-only REST client for api.india.delta.exchange
    store.py      Parquet fetch-through cache; ProductCatalog
    quality.py    synthetic-bar, halt, and tradability masks; universe screen
  research/       eight pre-registered experiments + registry + stats
```

Sizes: 3,578 lines in the core (`deltabt/` excluding `research/`), 5,134 in
`research/`, 2,009 in `tests/`. **172 tests pass** (4.4 s).

The processing model is the thing that matters for V1:

```
Parquet (whole history)  ->  build_signals(df, params)  ->  arrays of length N
                                                              |
                                       run_backtest() python loop over i in range(N)
```

Every indicator is computed over the complete history in one call. The engine then
walks the arrays. **Nothing in this design carries state across process
invocations, and nothing consumes data incrementally.** That is the central gap.

---

## 2. Existing reusable components

Ranked by how much of V1 they save.

| Component | Reuse | Notes |
|---|---|---|
| `deltabt/indicators.py` | **Verbatim** | Pine-exact, numba, tested against hand-computed Wilder values. The single most valuable asset — it is the reason live signals can be made provably identical to backtest signals. |
| `deltabt/costs.py` | **Verbatim** | `SymbolCosts.from_spec`, GST multiplier, `contracts_for` (integer rounding), `round_price`, `cost_per_r`, `funding_timestamps`. The paper broker needs all of it. |
| `deltabt/data/store.py::ProductCatalog` | **Verbatim** | Per-symbol tick size, contract value, fees, funding interval, position limit. V1 must not hardcode any of these — they differ per symbol. |
| `deltabt/data/client.py` | **Verbatim, for backfill** | Read-only, no auth, correct backward pagination. Becomes the warm-up and gap-fill path. Read-only by construction, which is exactly the safety property V1 wants. |
| `deltabt/data/quality.py` | **Verbatim** | `synthetic_mask`, `halt_mask`, `tradable_mask`. The halt-detection rule (≥20 flat zero-volume bars, plus the reopen bar) is already the logic §"Exchange halts" asks for. |
| `strategy.py::resample_ohlcv` | **Verbatim** | Verified to reproduce the exchange's own 5m bars exactly. Becomes the 1m→5m builder's offline twin for tests. |
| `strategy.py::_broadcast_confirmed` | **Concept** | The last-closed-bar projection idiom. V1 inverts the direction (see §3) but the non-repainting discipline is the same. |
| `wpr_latch.py::step_state` | **Verbatim if the latch is used** | Already written as an incremental one-bar step for exactly this reason. |
| `research/registry.py` | **Pattern** | Append-only JSONL discipline; the same "never overwrite a record" rule should govern the event log. |
| `deltabt/engine.py` | **Reference only** | Its exit-precedence, funding-snapshot and ambiguity logic are the spec the paper broker must match, but the code is a whole-array loop and cannot be reused as-is. |

---

## 3. Existing strategy implementation

Two implementations of the same family exist, and they are **not** interchangeable.

**(a) `deltabt/strategy.py` — the parametric one.** `StrategyParams` with
`mode="parity"` (the Pine script warts-and-all) or `"corrected"`. Configurable
Supertrend/DMI/WPR, percentile or absolute ADX thresholds, edge trigger, cooldown,
cost-per-R gate.

**(b) `deltabt/research/hwpr.py` — the frozen one.** H-WPR-1's pre-registered
conditions at ST(10, 2.0), ADX 28 / DI 14, WPR 140, ADX ≥ 25, with arms A–E and
three WPR variants. This is the closest existing code to the V1 brief.

### Three discrepancies between the brief and the code

These change what gets built and I cannot resolve them by inference.

**3.1 — The timeframes are inverted.** The brief specifies
`timeframe_primary: 5m`, `timeframe_confirmation: 1m`. Every existing
implementation is the opposite: the **base** grid is 1m and 5m supplies confirmed
trend context. `StrategyParams.validate()` actively rejects the brief's shape —
it requires `confirm_minutes > base_minutes`.

This is not a relabelling. It changes the evaluation instant and therefore the
trade count. Two readings:

- **(i) 5m-gated.** Evaluate the whole rule set once per closed 5m bar. The 1m
  confirmation is read at that instant. → at most 12 signals/hour/symbol.
- **(ii) 5m-armed, 1m-triggered.** The 5m close arms a setup; the bot then watches
  each closed 1m bar and enters on the first one that confirms, within some
  expiry. → up to 5× more entries, and it needs an arming-expiry parameter that
  the brief does not specify.

Reading (ii) is what most discretionary traders mean by "5m setup, 1m entry", and
it is what your original Pine script structurally did. Reading (i) is simpler and
strictly safer. **I need your answer before Phase 3.**

**3.2 — "Williams %R 140" names a period, not a rule.** Four distinct rules have
been on the table in this project, and they differ by an order of magnitude in
firing rate:

| Rule | Definition | Status |
|---|---|---|
| Original Pine | `prev < −80 and wpr > prev` (uptick out of the extreme) | ~4 trades/346 days at length 140 — effectively never fires |
| Traverse latch | arm below −80, fire on cross up through −20, expiry 30 bars | `wpr_latch.py`; measured **worse** than off at 15m |
| H-WPR-1 variant A | `wpr > −80 and rising` | the frozen H-WPR-1 baseline |
| H-WPR-1 variant C | `crossover(wpr, −80)` | pre-declared alternative |

**Recommendation: H-WPR-1 variant A**, because it is the one that was actually
pre-registered and measured at period 140, so V1's live behaviour will be
comparable to a known backtest. But this is your setup definition, not mine —
confirm or override.

**3.3 — ADX thresholds must be absolute in live.** `strategy.py::_threshold`
resolves a percentile over the **entire array**, which is non-causal and cannot
exist in a live system. The brief specifies `minimum: 25` (absolute), which is
correct and sidesteps this. V1's config loader must **reject** percentile
thresholds outright rather than approximate them from a trailing window.

---

## 4. Existing Delta market-data implementation

**REST only. There is no WebSocket client, no async code, and no streaming
anything.** `grep` for `websocket|wss://|asyncio|fastapi|sqlalchemy` across
`deltabt/` and `tests/` returns nothing. Neither `websockets` nor `fastapi` is
installed; the runtime is Python 3.10.12.

What the REST client already gets right, each verified against the live API:

- Candle responses cap at 4000 bars and truncate on the **old** side, so
  pagination walks `end` backwards. The termination condition is `oldest <= start`
  — row count is **not** a valid stop condition (a capped page returned 3,997 rows
  spanning 3,996 minutes on XRPUSD; treating that as "no more data" silently
  truncated 17,000 bars until it was fixed).
- Results arrive newest-first; `time` is bar-**open** in unix **seconds**, UTC.
- The currently-forming bar is included and is dropped.
- `MARK:` and `FUNDING:` series prefixes; `MARK:` carries no synthetic bars.
- Rate limiting at 8 req/s with `X-RATE-LIMIT-RESET` honouring.
- No API keys anywhere. Nothing in this repository has ever signed a request.

What is known about the WebSocket but not implemented: `wss://socket.india.delta.exchange`
was confirmed reachable (101 Switching Protocols) with a `v2/ticker` channel
during research. **The subscription payloads, message schema, heartbeat cadence
and reconnect semantics have not been characterised.** That is genuine unknown
work and I have priced it as such in Phase 2.

---

## 5. Existing tests

172 passing across eight files.

| File | Covers |
|---|---|
| `test_indicators.py` | RMA seeding, RMA through NaN, first-bar true range, WPR definition/warm-up/degenerate range, DMI bounds, Supertrend direction convention |
| `test_wpr_latch.py` | consume-on-fire, expiry restamping, no-fire-without-arm, exact-threshold-touch, NaN warm-up, FSM vs vectorised equality |
| `test_strategy.py` | HTF broadcast uses last closed bar and never leaks the current one, UTC-aligned resampling, parity uptick rule, edge-trigger plateau collapse, warm-up blanking, grid construction |
| `test_engine.py` | GST, integer contract rounding, position limits, tick rounding, cost-per-R, funding grid anchored to UTC epoch, leverage cap, **stop triggers on mark not LTP**, **same-bar conflict resolves pessimistically**, maker fee on target exit, cost gate, untradable-bar skip, max hold, cooldown |
| `test_hcompress/hfunding/hpair/hwpr.py` | per-experiment look-ahead and convention proofs, including future-data perturbation tests |

**What is already proven and can be inherited:** indicator correctness, HTF
non-repainting, mark-vs-LTP stop triggering, pessimistic same-bar resolution,
cost/rounding arithmetic.

**What has no coverage because it does not exist:** everything stateful — candle
assembly from ticks, reconnection, duplicate/out-of-order messages, persistence,
restart recovery, reconciliation, idempotency, health/readiness, halt state
transitions.

---

## 6. Missing components

Everything live. Grouped by the pipeline in the brief:

1. **WebSocket client** — connect, subscribe, heartbeat, detect stale, reconnect with backoff, resubscribe.
2. **Market-data normalisation** — one internal tick/quote record from Delta's channel payloads; LTP and **mark** kept distinct.
3. **Candle builder (1m)** — assemble closed bars from ticks; emit exactly once; detect gaps.
4. **Candle builder (5m)** — aggregate from closed 1m bars, UTC-aligned.
5. **Backfill/warm-up** — REST history on startup and after any gap.
6. **Streaming strategy engine** — evaluate on closed bars only; emit a full explanation, not a boolean.
7. **Risk engine** — the twelve configurable limits, sizing, RR enforcement, rejection recording.
8. **Paper broker** — deterministic fills, no order-placement capability anywhere in the process.
9. **Position manager** — lifecycle, R tracking, exit reasons.
10. **Persistence** — PostgreSQL, twelve tables, full event history.
11. **Recovery + reconciliation** — rebuild state from the database; verify internal consistency.
12. **Notifications** — provider abstraction.
13. **Health/metrics/API** — `/healthz`, `/readyz`, `/metrics`.
14. **Dashboard.**
15. **Deployment** — Dockerfile, compose, Kubernetes, Helm.

---

## 7. Proposed V1 architecture

### 7.1 The central decision: recompute, don't stream

A live bot can compute indicators two ways. This choice determines whether V1's
signals provably match the backtest or merely appear to.

- **Incremental state machines** — carry RMA/ATR/Supertrend state across bars.
  Fast, and the conventional choice. It is also where live bots silently diverge
  from their backtests: a seeding difference or a NaN-handling difference produces
  a small, permanent, invisible drift.
- **Bounded-window recompute** — on each closed bar, take the trailing W bars from
  an in-memory ring buffer and call `deltabt.indicators` **unchanged**.

**I propose bounded-window recompute.** With W = 1,500 5m bars across 4 symbols,
one recompute every 5 minutes costs single-digit milliseconds under numba. In
exchange, the live path executes literally the same functions as the backtest, so
divergence is structurally impossible rather than something we test for and hope.
For a system whose entire purpose is trustworthiness, that trade is not close.

Two conditions must hold and both are enforceable:

- **Window invariance.** Every indicator must give identical values at the window
  tail for W and 2W. This is true of Wilder RMA only after sufficient warm-up, so
  W must exceed the warm-up by a wide margin. **This gets a dedicated test**
  asserting bit-equality at the tail across window lengths.
- **No non-causal statistics.** Percentile thresholds are forbidden (§3.3).

Warm-up arithmetic for the V1 rule set at a 5m primary:
`max(WPR 140, DI 14 + 2×ADX 28 = 70, ST 10) + margin` ≈ **145 5m bars ≈ 12.1
hours**. Proposed `W = 1500` 5m bars (~5.2 days) and a startup backfill of **7
days** of 1m history per symbol — roughly 10,080 bars, three REST pages.

### 7.2 Process and concurrency model

**One process, one asyncio event loop, one instance.** No worker pool, no message
broker, no distributed anything. The state that matters is small and the
correctness cost of concurrency is high.

```
asyncio loop
├─ ws_task(symbol_group)   # one connection, all symbols on it
├─ tick_router             # -> candle builders
├─ bar_task                # on closed bar: indicators -> strategy -> risk -> broker
├─ watchdog_task           # freshness, gap detection, halt state
├─ persistence_task        # bounded queue -> Postgres (batched, never lossy)
└─ api_task                # aiohttp/FastAPI: /healthz /readyz /metrics /dashboard
```

Numba calls are synchronous and sub-millisecond; they run on the loop directly
rather than in an executor, which keeps ordering deterministic.

**Single-instance enforcement is a database advisory lock, not a deployment
setting.** `replicas: 1` and `strategy: Recreate` are necessary but they are
Kubernetes promises, and a manual `kubectl scale` or a botched rollout breaks
them. At startup the bot takes `pg_try_advisory_lock(<constant>)` on a dedicated
connection and **refuses to start** if it is held. Two bots then cannot both run
even if two pods exist.

### 7.3 Package layout

```
app/
  config/          pydantic settings, YAML strategy config, config versioning + hashing
  market_data/     delta_ws.py, normalize.py, candle_builder.py, backfill.py, gap.py
  indicators/      window.py (ring buffer) — computation delegates to deltabt.indicators
  strategy/        rules.py, engine.py, explanation.py
  risk/            limits.py, sizing.py, engine.py
  execution/       paper_broker.py, fill_model.py, intents.py
  portfolio/       position.py, manager.py, journal.py
  persistence/     models.py, repository.py, migrations/
  notifications/   base.py, console.py, webhook.py
  monitoring/      health.py, metrics.py, logging.py
  api/             app.py, routes.py, dashboard/
tests/
  unit/ integration/ safety/ recovery/
deploy/
  docker/ kubernetes/ helm/
docs/
  architecture.md strategy.md risk.md operations.md failure_modes.md
```

`deltabt/` stays where it is and is imported as a library. **No existing module is
rewritten.** Two small additive changes only (§11).

### 7.4 The safety boundary

The brief resolves the question I had left open: the exchange adapter exposes
market data only, with **no order-placement method at all** — not a method gated
behind a flag. That is the right call and it is stronger than a flag for a reason
worth stating: a flag is a runtime value that a later edit, an environment
variable, or a config mistake can flip, whereas a method that does not exist
cannot be called by any of those. Enforced three ways:

1. `app/market_data/delta_ws.py` and `deltabt/data/client.py` contain no signing
   code, no private endpoints, no `POST`/`PUT`/`DELETE`, and no API-key
   configuration. The REST client is `GET`-only today and stays that way.
2. **A test that greps the shipped source** for `place_order`, `submit_live`,
   `send_signed`, `api_key`, `api_secret`, `hmac`, and any non-GET HTTP verb
   directed at the exchange host, and fails the build on a hit. Cheap, and it
   catches the drift that review misses.
3. The container ships no credential mount, and `.gitignore` already blocks
   `.env`, `*.pem`, `*.key`, `credentials*`, `secrets*`.

### 7.5 Layer contract

```
Strategy  --emits-->  Signal (never an order; has no access to the broker)
Signal    --into-->   RiskEngine.evaluate()
                        ├─ approved -> ApprovedOrderIntent (only class the broker accepts)
                        └─ rejected -> SignalRejection (persisted, alerted, shown)
Intent    --into-->   PaperBroker.submit_order()
Fill      --into-->   PositionManager
```

`PaperBroker.submit_order` accepts **only** an `ApprovedOrderIntent`, a frozen
dataclass constructed nowhere except inside the risk engine and carrying the ID of
the risk evaluation that produced it. A strategy cannot construct one. This is a
type-level boundary, not a convention.

### 7.6 Live execution realism

Three points where a live paper broker can be *more* honest than the backtest was,
and one where it must not be:

- **Stop triggering.** Stops trigger on **mark price**; fills price off LTP. The
  `v2/ticker` channel carries both. Same rule the engine already enforces and
  tests.
- **Same-bar ambiguity disappears — but only forward.** In backtest, a 1m bar
  containing both stop and target is unorderable, which is why the engine resolves
  pessimistically and counts the ambiguity. Live, we see the **tick sequence**, so
  the true ordering is observable. V1 should use it, and record that it did. The
  bar-replay path (used in tests and backfill) keeps the pessimistic rule.
- **The look-ahead regression test still applies.** The specific bug — a maker
  entry filled because the bar's low touched the limit, then claiming that same
  bar's high as a target hit — produced 356 same-bar target exits against 1
  same-bar stop before it was found. Live, the tick sequence makes this
  impossible; in replay, it must be structurally impossible. The test is written
  against the **replay** path and asserts that entry-bar target fills cannot occur
  on a maker entry. Live gets a stronger test: the target fill's tick timestamp
  must be strictly greater than the entry fill's.

### 7.7 Idempotency

Key: `sha256(symbol | closed_bar_open_ts | direction | strategy_config_hash)`,
where `strategy_config_hash` is a hash of the **fully resolved rule set**, not a
version string — a version label that someone forgets to bump is exactly how a
config change becomes invisible in the audit trail.

Uniqueness is enforced by a **database unique constraint** on the signals table,
not by an in-memory set. An in-memory set is empty after a restart, which is the
precise moment duplicates matter most.

---

## 8. Data flow

```
Delta WS  ──ticks──▶  normalize  ──▶  1m builder ──closed 1m bar──▶  5m builder
                                           │                             │
                                           ▼                             ▼
                                      ring buffers  ◀────────────────────┘
                                           │
                              (on each closed PRIMARY bar)
                                           ▼
                              deltabt.indicators over trailing W
                                           ▼
                              StrategyEngine.evaluate() ──▶ Signal + full explanation
                                           ▼
                              RiskEngine.evaluate()
                                  ├── rejected ──▶ SignalRejection ──▶ DB + alert + dashboard
                                  └── approved ──▶ ApprovedOrderIntent
                                                        ▼
                                                  PaperBroker ──▶ Order ──▶ Fill
                                                        ▼
                                                  PositionManager
                                                        ▼
                                    every step ──▶ event log (Postgres) ──▶ dashboard / alerts

Live ticks also feed PaperBroker.process_market_event() continuously, which is what
triggers resting stops and targets between bar closes.
```

Note the two entry points into the broker: **bar-close** creates orders,
**per-tick** manages open ones. Stops must not wait for a bar close.

---

## 9. State machines

**Signal**

```
DETECTED ─▶ EXPLAINED ─▶ RISK_PENDING ─┬─▶ REJECTED (terminal, with reason)
                                       └─▶ APPROVED ─▶ ORDER_CREATED
                    └─▶ SUPPRESSED (halt / stale data / not ready) (terminal)
                    └─▶ DUPLICATE (idempotency key already present) (terminal)
```

**Order** (paper)

```
NEW ─▶ WORKING ─┬─▶ FILLED ─▶ (position opened)
                ├─▶ CANCELLED (expiry, halt, safety, manual)
                └─▶ EXPIRED (limit not touched within the configured window)
```

**Position**

```
OPENING ─▶ OPEN ─┬─▶ CLOSING ─▶ CLOSED(reason)
                 └─▶ SUSPENDED (halt / data failure) ─▶ OPEN | CLOSED(SYSTEM_SAFETY)
```

`SUSPENDED` is the state that makes halt handling honest: the position still
exists and its stop is still recorded, but the bot declines to claim it was
triggered during a window in which the exchange would not have triggered it.

Exit reasons, per the brief: `STOP_LOSS`, `TAKE_PROFIT`, `MANUAL_CLOSE`,
`TIME_EXIT`, `SYSTEM_SAFETY`, `DATA_FAILURE`.

---

## 10. Failure and recovery model

| Failure | Detection | Response |
|---|---|---|
| WS disconnect | socket close / exception | reconnect, exponential backoff + jitter, cap 60 s; `/readyz` false |
| **Stale WS** (open socket, no data) | last message > 30 s | force-close and reconnect — treated as worse than a clean disconnect, because it is invisible to every process-level probe |
| Missing 1m candle | builder sees a bar-open gap | REST backfill the gap; if unfillable, `DATA_FAILURE` |
| Duplicate message | sequence/timestamp already seen | drop; `data_duplicate_total` |
| Out-of-order message | timestamp < last processed | drop if its bar is already closed; never reopen a closed bar |
| Delayed message | arrives after its bar closed | drop with a counter; **never retro-edit a closed bar**, because a signal may already have been emitted from it |
| Exchange halt | ≥20 flat zero-volume 1m bars (`quality.halt_mask`) | `HALTED`: suppress signals, `SUSPEND` positions, alert |
| Post-halt reopen | first real bar after the run | **excluded from trading**, per the measured +0.32% one-minute auction gap on 2026-04-12 |
| Process restart | startup | rebuild from DB; backfill; `/readyz` false until reconciled |
| Postgres unavailable | write failure | **stop trading immediately**; unhealthy. An unrecorded trade is worse than a missed one |
| Clock skew | bar boundary vs local time | timestamps come from the exchange; local clock is used only for staleness |
| Reconciliation mismatch | derived state ≠ journal | refuse to resume, alert, require operator action |

**The recovery invariant:** state is rebuilt by replaying the persisted event
journal, never by trusting a cached snapshot. Snapshots exist only as an
optimisation and are always verified against the journal on load.

**`/healthz`** returns 200 only if: last WS message < 30 s, last closed 1m candle
< 90 s, no unexplained gap in 5 minutes, database writable, broker state internally
consistent. Otherwise **503**.

**`/readyz`** returns 200 only after: startup complete, backfill complete,
indicators past warm-up, both builders synchronised, state loaded, no unresolved
recovery condition.

### What V1 can and cannot establish

Worth stating plainly before we build it. H-WPR-1 fired **12.4 trades/day** across
four symbols at a 1m base; a 5m primary will fire materially less. At even 10
trades/day, a month of forward paper testing is ~300 trades — enough to detect a
gross expectancy difference of roughly 0.1R, and nowhere near enough to
distinguish a real edge from noise at this strategy's measured effect sizes.

So the forward test's job is to answer: *does the bot fire when the rules say it
should, size correctly every time, never exceed a limit, never duplicate a
position, and survive restarts and disconnects?* Those are answerable in a month.
"Is this profitable" is not, and V1 should not be read as evidence either way.

---

## 11. Files to create and change

### Changed (2 files, both additive)

- **`pyproject.toml`** — add a `live` extra: `websockets`, `fastapi`, `uvicorn`,
  `sqlalchemy[asyncio]`, `asyncpg`, `alembic`, `pydantic-settings`,
  `prometheus-client`, `structlog`. The existing `deltabt` install is unaffected.
- **`deltabt/config.py`** — add the WebSocket URL constant and a
  `StrategyParams` factory expressing the V1 rule set. **No existing field or
  default is modified.**

`indicators.py`, `costs.py`, `data/*`, `engine.py`, `strategy.py`, `wpr_latch.py`
and all of `research/` are **untouched**.

### Created

~45 files. By phase:

- **P2 market data (7)** — `delta_ws.py`, `normalize.py`, `candle_builder.py`, `backfill.py`, `gap.py`, `indicators/window.py`, `config/settings.py`
- **P3 strategy (3)** — `strategy/rules.py`, `strategy/engine.py`, `strategy/explanation.py`
- **P4 risk (3)** — `risk/limits.py`, `risk/sizing.py`, `risk/engine.py`
- **P5 execution (5)** — `execution/intents.py`, `execution/paper_broker.py`, `execution/fill_model.py`, `portfolio/position.py`, `portfolio/manager.py`
- **P6 persistence (4)** — `persistence/models.py`, `repository.py`, `migrations/`, `portfolio/journal.py`
- **P7 recovery (2)** — `recovery.py`, `reconciliation.py`
- **P8 dashboard/alerts (5)** — `api/app.py`, `api/routes.py`, `api/dashboard/`, `notifications/base.py`, `notifications/console.py`
- **P9 health (3)** — `monitoring/health.py`, `metrics.py`, `logging.py`
- **P10 deploy (7)** — `Dockerfile`, `docker-compose.yml`, k8s manifests, Helm chart
- **docs (5)** + **tests (~12 modules)**

### Configuration

`config/strategy.yaml`, matching the brief's shape and hashed into every signal's
idempotency key:

```yaml
strategy:
  timeframe_primary: 5m
  timeframe_confirmation: 1m
  supertrend:  {atr_period: 10, multiplier: 2}
  adx:         {period: 28, di_period: 14, minimum: 25}
  williams_r:  {period: 140, rule: <PENDING §3.2>}
risk:
  equity: 10000
  risk_per_trade: 0.005
  minimum_rr: 2.0
  max_open_positions: 1
  max_daily_loss: 0.02
  max_consecutive_losses: 3
  max_trades_per_day: 6
  max_notional_exposure: 30000
  cooldown_after_trade_minutes: 15
  cooldown_after_loss_minutes: 60
```

---

## 12. Implementation plan

| Phase | Deliverable | Exit criterion |
|---|---|---|
| **1** | This document | Your approval, and answers to §3.1 / §3.2 |
| **2** | WS client, normalisation, 1m + 5m builders, backfill, gap detection | 24 h continuous capture with zero unexplained gaps; locally built 1m bars match REST-served bars **exactly** for the same window |
| **3** | Strategy engine + explanation objects | Replaying historical bars through the live engine reproduces the H-WPR-1 backtest's signals **bar-for-bar** |
| **4** | Risk engine | Every limit has a test that trips it; every rejection carries a reason |
| **5** | Paper broker + position manager | Look-ahead regression test passes; deterministic replay gives identical fills across runs |
| **6** | Postgres + audit trail | Every one of the ten "why?" questions answerable by SQL alone, with no reference to logs |
| **7** | Recovery + reconciliation | `kill -9` at each of the five specified moments → identical state on restart |
| **8** | Dashboard + alerts | All six panels live; notification provider swappable without touching the strategy |
| **9** | Health, metrics, watchdog | `/healthz` goes 503 within 60 s of a **silently stalled** feed, not merely a dead process |
| **10** | Docker, k8s, Helm | Runs unattended 7 days through at least one reconnect and one deploy |

Phases 2–5 are sequential. 6 can overlap 4–5. 8–10 can overlap 7.

---

## What I need from you before Phase 2

1. **§3.1 — timeframe semantics.** 5m-gated (i), or 5m-armed / 1m-triggered (ii)?
   If (ii), what is the arming expiry?
2. **§3.2 — the WPR rule at period 140.** My recommendation is H-WPR-1 variant A
   (`wpr > −80 and rising` for longs, mirrored for shorts) because it is the
   pre-registered rule that was actually measured at this period.
3. **Symbol set.** BTCUSD / ETHUSD / SOLUSD / XRPUSD is the corrected research
   universe and is my default.
4. **Postgres.** Managed instance, or a container in the same namespace?
5. **Starting paper equity.** $10,000 is the research default.

Items 3–5 have defaults I will proceed with unless you say otherwise. Items 1 and
2 genuinely block Phase 3 — they define what the bot considers a setup, and that
is your call, not mine.
