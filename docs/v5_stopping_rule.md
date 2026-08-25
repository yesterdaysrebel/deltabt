# V5 forward test — evaluation stopping rule

**Frozen 2026-08-25, before the first bar.** The experiment was registered
at 09:58:37 UTC and the bot bound to it at 09:58:43. Written while `n = 0`, so nothing
in it can have been chosen to fit an observed result. Any deviation is
documented as a deviation, not silently absorbed.

## Why this document exists

`docs/v3_stopping_rule.md` had to be written at `n = 31`, after the same run
had already been looked at twice and the two looks disagreed — five trades
moved the verdict from "looks promising" to "no signal". That is optional
stopping, and once it has happened the false-positive rate is not 5% and is
not knowable.

This one is frozen first. The stopping point, the decision rule, and — most
importantly — **the fact that this run is expected to conclude UNDECIDED** are
all recorded in advance.

## What is under test

| | |
|---|---|
| experiment | `SPEC-WPR-240-PAPER-20260825` |
| stack | `v5` (`deltabt-paper-v5`, database `deltabt_v5`) |
| strategy | `SPEC:wpr_only@240` → `wpr_only@240m@110eede40f13` |
| rule | Williams %R(140) rising above −80 on a **240-minute** bar. No Supertrend, no ADX, no confirmation timeframe. Stop 2 × ATR(10), target 2R |
| symbols | BTCUSD, ETHUSD, SOLUSD, XRPUSD |
| risk | 0.5% of equity per trade, up to 6 concurrent positions |
| image | `43964a717756c7e139f3d04b89e72e9e6d45a40d` |
| config hashes | strategy `110eede40f13…`, risk `0338a386c43d39a4`, execution `d00c6f3b9411c7d2`, composite `abc6511450371f7d` |

**The strategy is a `StrategySpec`, not a hand-written arm.** `SPEC:wpr_only@240`
resolves through `deltabt.catalog` to the same object the backtester ran, and
`deltabt.rulecore` evaluates it on both sides. What is deployed is what was
measured — structurally, not by parity testing after the fact.

Circuit breakers are **off** (`max_drawdown_pct = 1.0`). See *Why there is no
drawdown stop*, below.

## Stopping point (frozen)

**Evaluate once, at whichever comes first:**

1. **30 days** from registration — **2026-09-24**, or
2. evidence that the live arm's signals diverge from the backtest's.

No evaluation before that point, and none after. Reading the daily report for
**health** — process alive, `/readyz` passing, config not drifting, candles
arriving — is expected. Reading it for **performance** is what this document
exists to prevent.

## What this run can conclude, stated before it starts

The deployed cell produced this over 588 days, four symbols, live gates:

| | |
|---|---|
| trades | 353 (0.60/day at one slot; 0.87/day at the deployed six) |
| net R per trade | **+0.0557**, 95% CI **[−0.0391, +0.1559]** |
| gross R per trade | +0.1083 |
| cost | 0.0526 R per trade |
| win rate | 39.9% |
| return | +9.78%, maximum drawdown 5.94% |

The confidence interval **spans zero**. That is the starting position, not a
detail.

The implied per-trade standard deviation is **0.935 R**. At the deployed six
slots, 30 days is about **26 trades**:

    se        = 0.935 / sqrt(26) = 0.183
    95% CI    = ±0.359 R

**So the run cannot resolve anything smaller than ±0.36 R**, and the effect
being looked for is +0.056 R — seven times smaller than the noise. The
minimum effect detectable at 80% power is **0.51 R**. Resolving the backtest's
own estimate would take about **2,210 trades**, which at 0.87/day is **seven
years**.

**This run is therefore expected to conclude UNDECIDED, and that is not a
failure.** It is written down here because at day 30 a positive number will be
tempting to read as confirmation and a negative one as refutation, and neither
reading is available at this sample size.

## What the run is actually for

Not profit, and not expectancy. It is for the things 26 trades *can* settle:

1. **Does the live arm reproduce the backtest's signals?** Same spec, same
   `rulecore`, same bars — `tests/live/test_spec_arm.py` asserts this on cached
   data, and this run tests it against a live feed, an incomplete-bucket
   stream, and a trailing window instead of a full history.
2. **Does the deployment survive 30 days unattended?** Restarts, gaps, feed
   drops, the daily report, the drawdown breaker's latch.
3. **Is the trade rate what was predicted?** 0.87/day. A materially different
   rate means the live and backtest bar sets disagree, which is a defect
   regardless of P&L.

## Decision rule (frozen)

| outcome | condition at day 30 | action |
|---|---|---|
| **DIVERGENT** | any live signal that `rulecore` on the same bars does not reproduce, or a trade rate outside 0.5–1.3/day | **stop.** A defect, regardless of P&L. Fix, then start a NEW experiment id |
| **NO EDGE** | 95% CI on mean R lies entirely below zero — requires mean R < −0.36 | stop the arm; the entry family is not carried forward |
| **PROMISING** | 95% CI lies entirely above zero — requires mean R > +0.36 | **treat as surprising and provisional.** +0.36R is 6× the backtest estimate and 7× what the walk-forward showed; the likeliest explanation is a small sample, not a large edge. Extend under this same rule. Change nothing |
| **UNDECIDED** | anything else — **the expected outcome** | stop the arm and change the question. Not a reason to keep spending |

**UNDECIDED means stop, not continue.** Recorded now, because at day 30 it will
be tempting to read "not refuted" as "keep going".

## Why there is no drawdown stop

`max_drawdown_pct` stays at 1.0 for the duration.

A drawdown halt does not prevent losses; it **censors the sample**. It
conditions the mean on the account not already having gone badly, so the
result is no longer an unbiased estimate of anything. This was measured
directly on these strategies: the `tight` gate set appeared to lose far less
than ungated — $9,210 against $99,303 — but had stopped trading through
**70.5% of the window**. It had not avoided the losses. It had stopped
measuring. Adding a seven-day resume brought the loss back to $72,314 across
269 halts: the gates deferred losses, they did not prevent them.

This is paper money and the run's only product is information, so truncating
the sample costs the one thing it exists to produce.

## The prior this is tested against

Recorded so the forward result is read against what is already known.

**Walk-forward, four anchored out-of-sample blocks, this exact cell:**

| block | test window ends | trades | net R | gross R | symbols positive |
|---|---|---|---|---|---|
| 0 | 2025-08-24 | 111 | +0.0451 | +0.0959 | 3/4 |
| 1 | 2025-12-20 | 103 | +0.0971 | +0.1460 | 3/4 |
| 2 | 2026-04-16 | 102 | −0.0039 | +0.0422 | 2/4 |
| 3 | 2026-08-12 | 110 | −0.0344 | +0.0233 | 1/4 |

Gross stays positive in all four blocks, which is why this cell was selected —
but **2 of 7 tracked cells did that, and chance alone predicts 0.88** (p =
0.215). Gross decays monotonically after block 1, net is negative in the two
most recent blocks, and the count of symbols contributing positively falls
3 → 3 → 2 → 1. The selection is weak on its own terms.

**Selection premium across the sweep:** mean out-of-sample net R was **−0.0376**
against **+0.0935** in sample — a premium of **+0.1310**, larger than the edge
it purports to explain. Anything chosen by in-sample ranking should be assumed
to carry it.

**The sweep as a whole:** 576 cells, 218,171 trades, aggregate net −$701,499
of which $673,836 (96.1%) was fees — but gross was **also negative**, at
−$23,796 (−$0.11/trade). Four of the five surviving cells fail at *zero* cost.
The friction is real and is not the whole story.

**Break-even:** at 0.0526 R cost and a 2R target, the win rate that merely
breaks even is `(1 + 0.0526) / 3` = **35.1%**. Random entry at 2R wins 33.3%.
The backtest's 39.9% clears break-even by 4.8 points — on 353 trades.

## What may change during the run

Nothing that enters the strategy, risk, or execution hash. Specifically
forbidden until the stopping point: the %R period or threshold, the ATR
multiplier, the target R, the timeframe, the symbol set, the risk fraction,
`max_open_positions`, and the breaker settings. Each changes the composite
identity, `bind_experiment()` fails closed, and the sample resets to zero.

Permitted: operational monitoring, and fixing a defect that stops the bot
running at all — which, per `docs/aws_deployment.md`, means stopping the
experiment with a reason and starting a **new** id, not patching underneath a
running one.

<!-- FROZEN ABOVE THIS LINE -->
