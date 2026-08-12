# deltabt

Backtesting and **pre-registered strategy research** for
**Delta Exchange India** perpetual futures.

It exists to answer one question honestly: *does a systematic edge exist here,
and if not, why not?* It is deliberately built to be able to return "no" — and
across eight pre-registered hypothesis families, it has.

**Backtest and research only. No order placement, no API keys, no live
trading.** Every call in this codebase hits a public endpoint; there is no
authentication or signing code anywhere, by design.

## Why this repo might be useful to you

Most public trading repos show a strategy that worked. This one is a complete
**negative-result research record**: eight economically distinct hypotheses,
each frozen in writing before results were seen, each with null models, a
locked test set that was never opened, and an append-only registry of verdicts.

It also documents **six methodology errors found in our own work** — including a
same-bar look-ahead bug that manufactured a `+0.482R at t=32` false positive
before it was caught. If you are building a backtester, that section may save
you more time than the strategy code.

## Repository layout

```
deltabt/
  config.py        parameter dataclasses, venue constants, validation
  data/            REST client, Parquet cache, product catalog, quality screening
  indicators.py    Pine-exact supertrend / dmi / wpr / atr / rma (numba)
  costs.py         per-symbol fees x GST, funding schedule, contract rounding
  engine.py        event-driven backtest loop, mark-triggered stops
  metrics.py       bootstrap CIs, trade-count gating
  sweep.py         grid search + anchored walk-forward
  research/        the eight pre-registered experiments, nulls, registry, stats
app/               V1 live paper-trading bot (see below) -- reads deltabt, never writes it
  market_data/     Delta websocket, candle builder, halt detection, REST backfill
  strategy/        H-WPR-1 Variant A on closed bars, with a full explanation per bar
  risk/            twelve authoritative limits; the strategy cannot override any
  execution/       deterministic paper broker -- no exchange order path exists
  persistence/     PostgreSQL schema, repository, single-instance advisory lock
  monitoring/      data-freshness health, Prometheus metrics, JSON logging
  api/             /healthz /readyz /metrics + a self-contained dashboard
tests/             research suite + tests/live/ for the bot
deploy/            Dockerfile, compose, Kubernetes, ArgoCD
docs/              architecture, strategy, risk, operations, failure modes
out/               experiment registry and result tables (per-trade dumps gitignored)
```

## Quick start

```bash
pip install -e ".[dev]"

python -m deltabt.cli screen                      # which symbols are usable
python -m deltabt.cli backtest --mode parity      # reproduce the original strategy
python -m deltabt.cli backtest --mode corrected   # with the review fixes applied
python -m deltabt.cli wpr-curve                   # trade count vs WPR length
python -m deltabt.cli sweep                       # parameter grid
python -m deltabt.cli walkforward                 # out-of-sample validation

pytest -q                                         # 172 tests
```

Candles are cached to Parquet under `data/`, so re-runs are offline and
instant. Add `--offline` to forbid network access entirely.

## What it models that TradingView cannot

| | Why it matters |
|---|---|
| **Mark-price stop triggers** | Delta triggers stops on mark price by default, not last-traded. Testing a mark stop against LTP lows systematically mistimes exits. |
| **Integer contract rounding** | `size` is a whole number of contracts. On SOLUSD one contract is ~$76 of notional, which materially quantises sizing on a small account. |
| **Per-symbol fees** | Not uniform: 0.02/0.05% for most crypto perps, 0.02/0.02% for tokenised equities, 0.01/0.01% for metals — all times 1.18 for GST. |
| **Snapshot funding** | Charged only if the position is open at the settlement instant, never pro-rata. Interval is per-symbol: 8h for ~80 perps, 4h for ~140. |
| **Synthetic bar filtering** | Delta forward-fills illiquid minutes as `volume=0` bars rather than omitting them. DOGEUSD is ~44% synthetic, BTCUSD 0%. |
| **Maintenance halts** | Long flat runs followed by a gap-open auction (one measured at +0.32% in a single minute). Stops do not trigger during halts. |
| **Leverage cap** | Enforced, and position size is floored by a minimum stop distance. |
| **Basis-point slippage** | Not ticks. A fixed 2-tick slippage spans >100× in relative cost across symbols and would make cross-symbol rankings an artifact of tick size. |

## Two modes

**`parity`** reproduces the original Pine script including its flaws:
level-triggered entries, uncapped position sizing, the `close > supertrend`
condition, and WPR(140). Its purpose is a correctness check, not a
performance baseline — the author's TradingView tester produced **zero** closed
trades at these settings, so a faithful port must too. Run it first; if it
produces hundreds of trades, the port is broken and nothing downstream counts.

**`corrected`** applies the review findings: a stateful WPR traverse latch
(default length 14, off by default in the sweep), percentile-calibrated ADX
thresholds per timeframe, confirmed-value higher-timeframe reads,
edge-triggered entries with a cooldown, a reversal exit, a leverage cap, and a
**cost-per-R gate**.

## The cost gate

The single most important addition. A 1m Supertrend(10, 2.0) stop sits a median
~20 bps from entry, while a taker round trip on Delta India is 0.118% plus
slippage — roughly **0.5R**. That moves the break-even win rate at a 2R target
from 33.3% to over 53% before any signal has been evaluated.

`--max-cost-per-r` (default 0.15) rejects signals whose modelled round-trip
cost exceeds that fraction of R. **It is normal and informative for this gate
to reject everything at the original parameters.** Use `--no-cost-gate` to see
what would have happened without it.

## Reading the output

Trade count is printed first because it decides whether anything else means
anything. Results under 200 trades are labelled **NOT interpretable**: at a 2R
target and a 40% win rate, N=216 is the minimum for a two-sigma result and ~486
for a swept one. Below ~50 trades the 95% interval on E[R] is about ±0.42R,
wider than any edge worth trading.

The sweep will report **"no configuration qualified"** rather than crowning the
best of a bad field. Selection requires ≥200 pooled trades across ≥4 symbols.

`same-bar ambig.` is the fraction of trades where the stop and target both fell
inside one 1m bar. Those resolve pessimistically (stop first, matching Pine).
Delta serves no sub-minute history, so the optimistic bound is reported
alongside as an envelope rather than pretending to a point estimate.

## Findings so far

Measured on real Delta India candles, BTCUSD, 1,331,315 1m bars,
2024-01-23 → 2026-08-11.

**Parity check passed.** The original strategy produced **11 trades across 4
symbols in 2.5 years**, matching the author's TradingView result of zero. The
mechanism: conditioned on the full trend stack, `P(WPR(140) < −80) = 0.00003`
and median WPR(140) is −9.9. The oversold gate and the trend filter are
near-mutually-exclusive by construction.

**1-minute bars cannot carry this strategy at Delta's fees.** Round trip is
15.8 bps; clearing a 0.15 cost-per-R bar needs a median stop of ~105 bps.
Median stop distance at 1m:

| ST factor | 2.0 | 3.0 | 4.0 | 6.0 | 8.0 | 12.0 |
|---|---|---|---|---|---|---|
| median R (bps) | 8.7 | 12.2 | 15.7 | 22.4 | 29.0 | 41.6 |
| cost / R | 1.82 | 1.30 | 1.01 | 0.71 | 0.54 | 0.38 |

Even at factor 12 — six times the original — cost is still 0.38R. No indicator
setting fixes this; the timeframe is the problem.

**Raising the timeframe fixes cost and reveals no edge underneath.** WPR off,
ST(3.0, 10), cost gate on:

| base | trades | win % | E[R] | 95% CI | cost/R | median R |
|---|---|---|---|---|---|---|
| 1m | 56 | 28.6% | −0.243 | [−0.479, +0.021] | 0.123 | 122 bps |
| 5m | 367 | 34.6% | −0.077 | [−0.182, +0.031] | 0.118 | 131 bps |
| 15m | 401 | 32.4% | −0.078 | [−0.181, +0.027] | 0.107 | 143 bps |
| 1h | 180 | 31.1% | −0.049 | [−0.213, +0.134] | 0.077 | 218 bps |

Win rate sits at the 33.3% break-even for a 2R target at every timeframe and
every CI straddles zero. Supertrend + ADX trend-following is a coin flip on
this instrument over this period.

**WPR is harmful and is now off by default.** At 15m it cut the sample from 401
trades to 57 while worsening expectancy from −0.078R to −0.212R.

**Fee tiers differ enough to change what is feasible.** Metals (XAUTUSD,
PAXGUSD) charge 0.01%/0.01% versus 0.02%/0.05% on crypto — a maker-maker round
trip of ~2.4 bps against ~13.9 bps taker-taker on BTC. That is a 6.7× lower
hurdle. Both are clean on 1m data but only 116 and 173 days old, so there is
not yet enough history for a walk-forward.

## Why this stopped: the statistical ceiling

After the intraday result, swing horizons (4h–1d) were tested. Cost per R improves
50× (1.30 → 0.026) but the sample collapses (thousands of trades → 137). A
validation analysis on the real data settled whether that trade is worth making.

**The four majors are 1.5 independent instruments, not 4.** Mean pairwise return
correlation is 0.73 at every horizon from 1h to 20 days; PC1 explains 80%. At
trade level, all four hold a position simultaneously 91.6% of the time, with
mean cross-trade R correlation 0.434 → design effect 2.22. So **137 nominal 4h
trades are 62 effective observations**; 17 daily trades are 8.

**Minimum detectable effect is +0.43 to +0.83R per trade** at 80% power. Every
observed point estimate (+0.188, +0.350, −0.211, +0.059) sits far below it. A
genuinely real +0.20R edge would have been declared insignificant 82% of the time.

**Years of data needed** (4h base, four majors, 80% power, α=0.05):

| true edge | years |
|---|---|
| +0.30R | 4–20 |
| +0.20R | 9–44 |
| +0.10R | 37–176 |

**Overlapping-window regression does not rescue this.** Same signal, same data:
510 discrete trades give cluster-adjusted t = +0.57; 20,004 overlapping panel
observations give Driscoll-Kraay t = +0.59. They agree to 0.02. The naive
overlapping t of +4.54 is inflated 7–11× by serial correlation. There are 69
truly independent swing observations in this dataset and no estimator creates more.

**The trend system loses to random.** Exposure-matched random-entry controls
(2,000 sims, matched on trade count, holding period, long/short mix, costs):
on BTCUSD random *long-only* entries earn +0.0997R while the Supertrend system
earns +0.0933R. On XRPUSD, ~44% of the headline edge is drift rather than timing.
The correct null is not zero — it is −0.02 to −0.03R for cost drag, and the
long-only matched-exposure drift line per symbol.

**The required threshold now exceeds the available evidence.** Roughly 811
nominal parameter trials have been run across this project. Measuring the
eigenspectrum of the 72-cell grid P&L matrix shows cells are correlated at 0.63
and collapse to 3–6 independent bets — a 22× compression, giving M_eff ≈ 37–149.
A new result must therefore clear **t ≥ 3.2**. But t = Sharpe × √years, so the
maximum t a Sharpe-1.0 strategy can produce in 2.55 years is **1.60**.
*The bar is above the ceiling.* Detection today requires true Sharpe ≥ 2.53.

Separately, the t-table itself is unusable here: per-trade R has skew 14.3 and
kurtosis 272, and a bootstrap of the demeaned distribution gives
P(|t| > 1.96) = 14%, not 5%.

**Walk-forward is not diagnostic at this frequency.** The three OOS splits
(−0.062 / +0.240 / −0.214) have a spread smaller than expected under a *constant*
true edge; SE per split is ±0.33 to ±0.52. They were three coin flips. Making one
split resolve +0.20R would take 18 years.

**What would change the answer:** widen the cross-section to 20–30 genuinely
lower-correlated instruments (raises N_eff per unit time by 3–5×, the only lever
with real leverage), or source longer history — BTC/ETH have 8–10 years on other
venues, and Delta's 2024 listing date is not a constraint if the strategy is
priced off the underlying. Continuing to search parameter space on 2.55 years of
four correlated majors cannot produce a validated result, only a better-fitted one.

## The experiment registry

Every hypothesis was pre-registered before results were inspected. Verdicts are
ordinally distinct and deliberately not collapsed into "failed":

| Experiment | Mechanism | Trades | Gross | Net | Verdict |
|---|---|---|---|---|---|
| H-Scalp-1 / rev2 | extreme move → reversion | 3,101 | −0.054 R | −0.097 R | **NO EDGE** |
| H-Scalp-2 | extreme move → continuation | 2,466 | **+0.116 R** | −0.021 R | **NO ECONOMIC EDGE** |
| H-Compress-1 | vol compression → expansion | 169 | −0.155 R | −0.435 R | **NO SIGNAL** |
| H-Funding-1 | funding / crowding | 1,356 | −11.2 bps | −27.0 bps | **NO SIGNAL** |
| H-Pair-1 | XAUT/PAXG relative value | 12 | +1.51 bps | −74.1 bps | **NO SIGNAL** |
| H-WPR-1 | MTF trend + W%R, no pullback | 5,860 | +0.032 R | −0.351 R | **NO ECONOMIC EDGE** |
| H-TREND-1 | MTF trend, no W%R | 5,969 | +0.058 R | −0.345 R | **NO SIGNAL** |

`out/experiments.jsonl` is the append-only source of record. Records are never
overwritten — a superseded run stays, with its correction recorded beside it.

Three results are worth singling out:

- **The pullback was not the problem.** Removing it raised trade count from 6 to
  4,356 (726×) and made the original strategy measurable for the first time.
  Gross expectancy stayed at approximately zero and went negative out of sample.
- **Williams %R was not the problem either.** Removing it improved *training*
  gross (+0.032 → +0.058 R) while validation stayed negative in both. The
  Supertrend + ADX/DI + W%R family is now closed from both directions.
- **Entry filters here work as risk geometry, not prediction.** Adding 1m
  confirmation to a 5m regime improved cost/R twelvefold (5.04 → 0.40) with no
  statistically established gain in gross expectancy.

## V1 — live paper-trading bot (`app/`)

The research is finished and negative. `app/` is a different thing: a **24/7
live-market paper-trading bot** whose purpose is to validate an execution
engine, not to discover an edge.

```bash
pip install -e '.[live]'
docker compose -f deploy/docker/docker-compose.yml up -d db
DATABASE_URL=postgresql://paper:paper@localhost:5432/paper python -m app
# dashboard, /healthz, /readyz, /metrics on :8000
```

**It cannot place a real order.** No order-placement method exists in the
process, the exchange adapter is GET-only, there are no credentials, and there
is no live-trading flag. `tests/live/test_no_live_trading.py` enforces all of
that against the shipped AST — 483 assertions, negative-controlled against a
deliberate violation to prove it can fail.

| Piece | Notes |
|---|---|
| Market data | Delta WebSocket (`v2/ticker`, `candlestick_1m`, `all_trades`) + REST backfill. Socket timestamps are **microseconds**; REST is seconds |
| Candles | Bars close on observing a later `candle_start_time`, never the local clock except as a grace fallback. Closed bars are immutable. 5m is **derived** via the backtester's own `resample_ohlcv` |
| Strategy | H-WPR-1 Variant A, 5m primary / 1m confirmation, calling `deltabt.indicators` unchanged via bounded-window recomputation |
| Risk | 12 configurable limits, each with a test that trips it and a rejection naming the limit, its value and the observation |
| Execution | Deterministic paper broker. Stops trigger on **mark**, targets fill on **LTP**. Live fills carry the tick's microsecond timestamp, so stop-vs-target ordering is *observed* rather than assumed |
| Persistence | PostgreSQL. Idempotency is a UNIQUE CONSTRAINT, not an in-memory set — that set is empty exactly when duplicates matter most |
| Single instance | `pg_try_advisory_lock`, not `replicas: 1`. Verified to survive `kill -9` |
| Health | `/healthz` is **data freshness**, not process liveness. A bot with a dead socket looks healthy to every process-level probe and is worse than a crashed one |

Documentation: [architecture](docs/architecture.md) ·
[strategy](docs/strategy.md) · [risk](docs/risk.md) ·
[operations](docs/operations.md) · [failure modes](docs/failure_modes.md)

**The strategy V1 forward-tests was classified NO ECONOMIC EDGE.** It is used
because it is frozen and measured, so any live/backtest divergence is an engine
bug rather than an open question. A month of paper trading at this trade rate
can establish that the engine is trustworthy; it cannot establish profitability,
and the forward-test record should not be read as if it could.

## Caveats

- Indicators are a reimplementation. They are tested against hand-computed
  Wilder values and structural invariants, but if you want certainty, export a
  series from TradingView and diff it.
- The default grid is ~430 cells on purpose. With ~37 cells the expected
  maximum t-statistic under the null is already 2.5–2.8; `GridSpec.fine()`
  (~16,500 cells) will find a spurious winner almost by construction.
- A passing walk-forward is necessary but not sufficient evidence to trade real
  capital. Nothing here is financial advice.

## Licence

MIT — see [LICENSE](LICENSE).

## Disclaimer

Research software. Nothing here is investment advice. The eight experiments
recorded in `out/experiments.jsonl` found **no validated edge**; do not read
this repository as a trading recommendation. No live orders have ever been
placed by this codebase and it contains no order-placement capability.
