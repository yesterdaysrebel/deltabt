# Paper runner — strategy audit

Audit performed before any code was written, per §2. **No strategy file was
read for modification, refactored, or rewritten.**

## STRATEGY FROZEN — RUNNER WILL CONSUME THIS STRATEGY WITHOUT MODIFICATION.

---

## 0. Principal finding: the runner already exists

The task asks for a paper-trading runner to be built. **One is already
implemented, tested, deployed and running.** It is `app/`, it has been live on
AWS since 2026-08-15 under experiment `H-WPR-1-PAPER-AWS-V3-20260815-2`, and
yesterday's automated report shows it processing 1,399 evaluations, 43 orders,
27 fills and 11 closed trades with 0 application errors.

Building a second runner would create a parallel system that can silently
diverge from the one holding live state. This audit therefore maps the existing
implementation against each requirement, and §17 of this document lists the
genuine gaps, which are documentation artifacts rather than functionality.

---

## 1. Exact strategy identity

| item | value |
|---|---|
| module | `app/strategy/rules.py` |
| configuration | `app/config/variants.py` |
| deployed variant | `V3_WIDE_STOP` = `H-WPR-1-VariantA-WideStop` |
| selection | `DELTABOT_VARIANT` env var (`VARIANT_ENV`) |
| **strategy hash** | **`11461f2a11a96f8a`** |
| risk hash (defaults) | `58a7a452914bf93f` |
| execution hash | `d00c6f3b9411c7d2` |
| composite config hash | `2b4b19598a47d1e9` |
| app version | `1.0.0-paper` |

The strategy hash computed from this checkout matches the value the deployment
workflow already pins (`--expect-strategy-hash "11461f2a11a96f8a"` in
`.github/workflows/monitor.yml`), confirming the checkout and the running
experiment are the same strategy.

The deployed **risk** hash is `e8f737f6f287599b` rather than the default
`58a7a452914bf93f`, because the running experiment sets risk via environment
overrides. This is expected and documented in `scripts/daily_report.py`.

## 2. Configuration as frozen

    name                    H-WPR-1-VariantA-WideStop
    primary_timeframe       5m
    confirmation_timeframe  1m
    supertrend              atr_period 10, multiplier 2.0
    adx                     period 28, di_period 14, minimum 25.0
    williams_r              period 140, rule variant_a
    confirm_supertrend      True
    confirm_adx_di          True
    confirm_wpr             False
    fire_once               False
    target_r                2.0
    max_stop_pct            0.10
    window_bars             1500

`max_stop_pct = 0.10` is **not** configuration drift from the research
`max_stop_pct = 0.05`. `V3_WIDE_STOP` is a deliberately separate variant, and
`variants.py` says so explicitly: *"This is a SEPARATE VARIANT because
max_stop_pct is a strategy parameter, not a risk parameter."* It carries its own
hash. Nothing was changed.

## 3. Symbols and timeframes

    symbols       BTCUSD, ETHUSD, SOLUSD, XRPUSD
                  (deployment additionally carries BEATUSD, AKEUSD, BANKUSD)
    primary       5m
    confirmation  1m
    warm-up       window_bars = 1500 primary bars

## 4. Entry, exit, stop, target

Read but not modified; the authoritative statement is the code:

- **entry** — `app/strategy/rules.py`, Supertrend + ADX/DI on the primary
  timeframe, confirmed on the 1m timeframe. `confirm_wpr=False` in this variant.
- **exit** — stop and target only. **No time stop.** Yesterday's report flags an
  open position at 30.9h as a consequence; that is strategy behaviour and is not
  changed here.
- **stop trigger** — MARK price, matching Delta's default.
- **target** — resting limit at `target_r = 2.0`, fills on LTP at maker fee.
- **max stop** — 10% of entry, rejections recorded with the observed stop width.

## 5. Position sizing — external to the strategy

Sizing lives in `app/risk/engine.py`, not in the strategy:

    risk_amount = equity * risk_per_trade          (default 0.5% of 10,000)
    quantity    = costs.contracts_for(units)       (rounds DOWN)
    refusal     if the position rounds to zero contracts

This is documented here because §8 requires the source of position sizing to be
named when it is external to the strategy. It is reused exactly; nothing about
it is reimplemented.

## 6. Candle-close and state requirements

- Signals evaluate on **closed** primary bars; `candlestick_1m` streams the
  forming bar and the builder closes it.
- `fire_once=False` in this variant: the condition is level-triggered and
  re-arms after each position closes.
- Position state is persisted, not held in memory only (see §10 below).
- Higher-timeframe dependency: 5m primary derived from the 1m stream by
  `app/market_data/candle_builder.py`.

## 7. Existing backtest implementation

`deltabt/research/hwpr.py` (`_simulate`) is the frozen research simulator and is
listed under the program's do-not-modify constraints. It was not touched.

---

## 8. Existing runner architecture, mapped to the required one

    market data      app/market_data/delta_ws.py      (read-only websocket)
        |            app/market_data/candle_builder.py
        v            app/market_data/market_state.py
    strategy         app/strategy/rules.py
        |
        v
    signal           app/execution/intents.py
        |            app/risk/engine.py               (approve / reject)
        v
    paper execution  app/execution/paper_broker.py
        |            app/execution/order_state.py
        +--> simulated order / fill / position / fees / slippage / P&L
        |
        v
    journal          app/persistence/repository.py    (append-only, Postgres)
    metrics          app/monitoring/metrics.py
    reports          app/reports/builder.py

This is the architecture §4 specifies, already built.

## 9. Live-order safety — stronger than §5 requires, and in one place conflicting

`app/safety.py` is the single definition of the boundary. It forbids, by AST
scan over the shipped source:

    order-placement method names   place_order, submit_live_order, amend_order, ...
    credential identifiers         api_key, api_secret, signature, signing_key, ...
    signing libraries              hmac, ecdsa, nacl
    mutating HTTP verbs            post, put, patch, delete

`tests/live/test_no_live_trading.py` enforces it: **915 passed, 2 skipped**,
scanning every `*.py` in the repository — including every module written during
this program.

### Blocker on §5A and §5B, reported rather than improvised

§5 asks for `PAPER_MODE` defaulting to true, and refusal to start when
`PAPER_MODE=false`.

**`PAPER_MODE` is in the existing `FORBIDDEN_FLAGS` set and cannot be added.**
The rationale is recorded in `app/safety.py`:

> *"A flag-gated live mode is explicitly forbidden: the ABSENCE of the
> capability is the boundary, not a runtime toggle."*

Implementing §5A/§5B literally would:

1. fail `tests/live/test_no_live_trading.py`, which §14 forbids weakening; and
2. **reduce** safety, because a `PAPER_MODE` flag asserts that a live mode
   exists and is being suppressed — whereas today no order-placement code exists
   to suppress.

The intent of §5A/§5B — *the runner cannot place a real order* — is satisfied
more strongly by the current design. I have not added the flag. **This needs an
explicit decision before anything changes.**

§5C–§5G are already satisfied: `PaperBroker` is a separate interface, the
exchange adapter exposes market data only, no trading credentials exist
anywhere in the process, and both static and dynamic tests enforce it.

## 10. Restart and crash safety

`app/persistence/` with an advisory lock (`lock.py`) and `ConfigurationDrift`
in `app/forwardtest/identity.py`, which **fails closed**: if the running
configuration does not match the experiment already in the database, the bot
refuses to trade rather than adopting the new configuration or quietly
continuing on the old one.

`tests/live/test_recovery.py` — 41 passed.

## 11. Fill model

`app/execution/paper_broker.py`, deterministic and documented. Two paths:

- **live** (`process_market_event`) — ticks in arrival order, so stop-vs-target
  ordering is observed rather than inferred, with microsecond timestamps on the
  fill.
- **replay** (`process_bar`) — closed bars, resolves pessimistically (stop
  first), and carries a same-bar look-ahead guard added after a measured bug:
  356 same-bar target exits against 1 same-bar stop, a 356:1 asymmetry.

Execution assumption is **taker**, matching how the strategy was evaluated. §6
of the task requires exactly this and forbids assuming maker execution; the
maker research was not applied.

`tests/live/test_paper_execution.py` — 50 passed.

---

## 12. Genuine gaps against the task's deliverables

Functionality: none found. The gaps are artifacts this task names explicitly:

| deliverable | status |
|---|---|
| `reports/paper_runner_strategy_audit.md` | **created by this audit** |
| `config/paper_strategy_manifest.json` | **created** — see note below |
| `reports/paper_runner_readiness.md` | **created** |
| `reports/paper_trading_status.md` | already served by `forward-test report` and `scripts/daily_report.py`; a static snapshot is redundant and would go stale |
| `PAPER_MODE` flag | **BLOCKED** — see §9 |

The manifest is a **static mirror** of what `app/forwardtest/identity.py`
computes at runtime and stores in the database. The runtime identity remains
authoritative; the JSON file exists for auditability as §3 requests. Nothing
reads it, so it cannot cause drift.

---

## 13. Statement required by §2

**STRATEGY FROZEN — RUNNER WILL CONSUME THIS STRATEGY WITHOUT MODIFICATION.**

No file under `app/strategy/`, `app/config/`, `app/risk/` or
`deltabt/research/` was modified by this task.
