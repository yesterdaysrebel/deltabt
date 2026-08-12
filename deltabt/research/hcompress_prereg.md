# H-Compress-1 — Pre-registration (frozen before any result was inspected)

Recorded before running the strategy. Any deviation is documented as a
deviation, not silently absorbed.

## Question

Does volatility compression followed by confirmed expansion and a passive
retest contain a persistent **gross** edge large enough to survive realistic
Delta Exchange India execution costs?

## Eligibility rule (frozen — applied before any performance is computed)

A symbol enters the universe iff, measured over the study window only:

1. **Continuous listing** covering the entire study window (2025-01-01 → latest).
2. **Synthetic-bar rate ≤ 5%** of 1m bars over the study window.
   (Delta forward-fills untraded minutes as `volume=0`, `o=h=l=c`.)
3. **24h turnover ≥ $1,000,000** — a deliberately low bar, set to be inclusive
   rather than to select performers.
4. **≥ 180 days of history** before the study window start, so the trailing
   percentile has a warm-up that is not itself inside the study window.
5. No maintenance-halt coverage above 2% of bars.

Symbols are NOT selected on performance. The rule is applied once and the
resulting universe is fixed for every arm.

**Pre-filter justification (logical, not empirical-on-results):** liquidity on
this venue improves monotonically with time for every symbol measured
(BTC 11.1%→0.0%, ETH 13.7%→0.0%, SOL 62.3%→1.3%, XRP 82.6%→6.1% by half-year).
A symbol whose synthetic rate exceeds 5% in the most recent 30 days therefore
cannot achieve ≤5% over a window that also contains earlier, less liquid data.
Such symbols are excluded without fetching full history. This is conservative
in the sense that it can only shrink the universe, never admit a bad symbol.

## Data splits — TEST IS LOCKED

Chronological 60 / 20 / 20 over the study window.
Selection is permitted on VALIDATION only. **TEST must not be computed or
inspected** unless the train+validation criteria below are met.

## Primary arm (frozen)

| parameter | value |
|---|---|
| compression: 15m ATR(14)/close percentile | **20th** |
| percentile lookback | trailing 960 × 15m bars (10 days), strictly before t |
| minimum compression duration | **4 consecutive 15m bars** |
| range quality | compression_range / ATR(15m) ≤ **1.5** |
| expansion timeframe | 5m, closed candles only |
| expansion body | ≥ **0.50 × ATR(5m)** |
| expansion volume | ≥ **1.5 × 20-bar average** |
| expansion close | strictly beyond the compression boundary |
| entry | passive limit at the broken boundary (compression_high / low) |
| order lifetime | **3 × 5m bars**, then cancelled (never converted to taker) |
| stop | opposite side of the compression range |
| max stop distance | **2.0% of entry**; skip the trade if exceeded |
| target | **2R** (secondary 3R, both reported) |
| time exit | **24 × 5m bars** |
| risk | 0.5% of equity, $10,000 per symbol |
| max leverage | 10x |
| execution | maker entry, maker exit; stop and time exits pay taker |

## Mandatory execution rule

On the bar that fills a passive entry, the **stop may trigger but the target
may not**. Target evaluation begins on the following bar. Rationale: a maker
long fills when the bar's low reaches the limit, so that bar's high generally
occurred before the fill. This rule is enforced in code and covered by a
dedicated automated test.

## Execution comparison (added at the author's request, before results)

Two execution arms, both pre-registered:

- **Arm A — passive retest (PRIMARY).** As specified above.
- **Arm B — taker breakout (DIAGNOSTIC ONLY).** No retest wait: enter at market
  on the open of the bar following the confirmed expansion candle. Identical
  stop (opposite compression boundary), identical 2R/3R targets, identical time
  exit. Pays taker on entry and on every exit.

Arm B is not a candidate for deployment and is not eligible for the PROMISING
or ROBUST classifications. Its purpose is diagnostic: if B loses badly while A
approaches breakeven, that is evidence that **execution is part of the edge**
rather than an implementation detail. If both fail by a similar margin, the
signal itself is absent and execution is irrelevant.

Note the two arms are not cost-comparable one-for-one: Arm B enters beyond the
boundary, so its R (entry → far boundary) is larger, which *lowers* its cost/R
even though it pays the taker rate. Both effects are reported separately so the
comparison is not confounded.

The same-bar target prohibition applies to Arm A only. Arm B enters at a bar's
open, so that bar's full range genuinely follows the fill.

## Pre-declared sensitivity grid (108 arms, no additions permitted)

- compression percentile: 10 / 20 / 30
- minimum duration: 4 / 6 bars
- volume confirmation: 1.0× / 1.5× / 2.0×
- expansion body: 0.25 / 0.50 / 0.75 × ATR(5m)
- target: 2R / 3R

Multiple-comparison correction is applied to any selection across this grid.

## Null models (all constructed without future information)

- **A — random eligible compression events.** Real compression zones, but the
  expansion timestamp is replaced by a uniformly random eligible 5m bar; the
  same retest / stop / target geometry is applied.
- **B — shuffled expansion directions.** Real events, real timing, real
  geometry; the long/short label is randomly reassigned.
- **C — timestamp permutation within volatility regime.** Signals are moved to
  a different time carrying the same realised-volatility decile, preserving the
  volatility conditioning while destroying the compression→expansion sequence.

## Classification rule (decided in advance)

- **NO SIGNAL** — gross ≤ 0 on train and validation, or fails falsification.
- **REAL SIGNAL / NO ECONOMIC EDGE** — gross > 0 and survives falsification,
  but net < 0 after realistic costs.
- **PROMISING** — gross and net both > 0 on train *and* validation, with CIs
  and walk-forward consistent with a real edge.
- **ROBUST** — only if it additionally survives symbol diversity, long/short
  balance, cost sensitivity, nulls, and multiple-comparison correction.

Test set is opened only if PROMISING is reached on train + validation.
