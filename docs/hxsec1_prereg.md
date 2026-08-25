# H-XSec-1 — Pre-registration (frozen before any return was computed)

**Frozen 2026-08-23.** Recorded before the daily bars were pulled. Any
deviation is documented as a deviation, not silently absorbed.

## Why this hypothesis

Twelve tests in this program have returned nothing. They share one structure:
**predict direction, from price-derived features, on a single crypto perp, over
minutes to hours, at a fixed R multiple.** Every one landed on a win rate of
`1/(1+R)`. That is not twelve independent failures — it is one finding,
replicated twelve times.

This changes the structure rather than the indicator:

> **H-XSec-1.** Within the liquid Delta India perpetual universe, relative
> performance is persistent: symbols that outperformed their peers over the
> past two weeks continue to outperform over the next day, by enough to survive
> a daily-rebalance round trip.

Two things make it different from everything already tried:

1. **It is relative, not absolute.** It can pay while every symbol falls. No
   prior test here was cross-sectional; the one pair attempt was `n = 12`,
   which measured nothing.
2. **Friction is not the binding constraint for the first time.** This
   program's one solid positive finding is `cost_r = round_trip / stop_pct`,
   verified to four decimals across an 11× range of stop widths and again
   across five timeframes in H-Scalp-3. At a daily holding period the
   denominator is a 3–5% move rather than 0.3–1.5%, so a round trip is roughly
   **4% of a one-sigma daily move** against the 15–136% that killed the rest.
3. It has an **outside prior**. Cross-sectional momentum is among the most
   replicated anomalies in equities, with published crypto results. Every prior
   hypothesis here rested on a chart pattern.

## THIS TEST IS UNDERPOWERED AND THAT IS KNOWN IN ADVANCE

Stated before the run so the result cannot be over-read in either direction.

Measured during feasibility, before this document was written: of 220 listed
perpetuals, **16** were listed before 2025-01-01 *and* currently turn over
≥ $1M/day; **38** at a $250k floor with listings running to 2026; **7** at
≥ $5M.

Consequences, accepted:

- **Deciles are impossible.** A decile of ~20 names is two names. The primary
  portfolio is **terciles**, roughly 5–7 names a side.
- **Breadth, not sample length, is the limit.** Cross-sectional strategies earn
  their Sharpe from many weakly-correlated bets. Twenty crypto perps that all
  follow BTC are perhaps three or four independent bets.
- Over the ~600 available days this can detect roughly an **annualised Sharpe
  above 1.3**. A real-but-modest effect will return UNDECIDED, and that outcome
  **must not be reported as a refutation**. It is the same limitation that
  ended the v3 forward test, stated up front this time.

## Eligibility rule — CAUSAL, applied at every rebalance date

The obvious filter is a trap and is explicitly forbidden: selecting symbols on
**today's** turnover would pick the ones that survived and are liquid *now*,
which is survivorship bias inside a 2025 backtest. Liquidity is therefore
computed from the candles themselves, strictly before each rebalance.

A symbol is eligible at rebalance date `d` iff, using bars strictly before `d`:

1. **≥ 90 daily bars** of history.
2. **Trailing 30-day median daily USD volume ≥ $250,000.**
3. **≤ 20% zero-volume days** in the trailing 30 (Delta forward-fills untraded
   days as `o=h=l=c, volume=0`; a symbol that is mostly synthetic is not
   tradeable at any price).
4. **Crypto perpetuals only** — the 0.05% taker tier. The 31 tokenised-equity
   and 3 metals perps track instruments with different trading hours, so their
   daily bars are not comparable to a 24/7 crypto bar.

The universe is an **unbalanced panel**: symbols enter as they list and
qualify, and leave when they stop qualifying. Symbols are never selected on
performance.

## Primary arm (frozen — one cell, fixed before the run)

| parameter | value |
|---|---|
| feature | cumulative log return over **14 days, skipping the most recent day** (`d−15 → d−1`) |
| ranking | all eligible symbols at `d`, ascending |
| portfolio | long top tercile, short bottom tercile, **equal weight, dollar-neutral** |
| rebalance | daily at 00:00 UTC |
| holding | one day |
| costs | realised turnover × **0.079% per side** (taker 0.05% × 1.18 GST + 2 bps slippage). Taker on both sides — conservative; no maker fills assumed |
| funding | not modelled in the primary arm; reported separately as a diagnostic |

**Skip-1 is part of the primary, not a tuned choice.** The most recent day is
excluded because short-term reversal is a distinct, well-documented effect that
would otherwise contaminate a momentum signal. It is declared here so it cannot
later look like a fitted parameter.

## Pre-declared robustness grid — DO NOT EXPAND

- lookback: 7 / 14 / 30 days
- buckets: terciles / quintiles
- skip-1: on / off
- liquidity floor: $250k / $1M

`3 × 2 × 2 × 2 = 24` cells. The primary is `14d / terciles / skip-1 / $250k`.
Every other cell is a robustness check, not a candidate. **A result that
appears only in a non-primary cell is noise and is recorded as such.**

## Pre-declared secondary arm (reported, NOT primary)

**1-day cross-sectional reversal** — long yesterday's worst tercile, short
yesterday's best. This is a *different hypothesis* in the opposite direction,
declared now so that if momentum fails and reversal works, the reversal result
is visibly a second test rather than a rescued first one. It carries the same
multiple-comparison discount and cannot be promoted above UNDECIDED without its
own fresh window.

## Data splits — TEST IS LOCKED AND GENUINELY CLEAN

- **train** 2025-01-01 → 2025-07-01
- **valid** 2025-07-01 → 2026-01-01
- **test** 2026-01-01 → data end **[LOCKED]**

Unlike H-Scalp-3, this test window is **untouched for this family** — no
cross-sectional strategy has been run on this data. Test is computed **only
if** train and validation are both positive at the primary cell. Selection
across the robustness grid happens on **validation only**.

## Primary metric

Annualised Sharpe of the **daily net long-short return series**, with a
stationary-bootstrap confidence interval (daily crypto returns are
autocorrelated and heteroskedastic; a plain t-test overstates significance).
Mean daily net return and its bootstrap `t` are reported alongside.

Gross and net are always reported separately, because the entire point of the
horizon change is that friction should no longer dominate. **If gross Sharpe is
≤ 0, the cost result is irrelevant and the answer is NO SIGNAL.**

## Classification rule (decided in advance)

Evaluated on train and validation at the primary cell:

| verdict | condition |
|---|---|
| **PROMISING BUT UNPROVEN** | net Sharpe > 0 in train **and** validation, gross Sharpe > 0 in both, and net survives a 1.5× cost stress |
| **NO ECONOMIC EDGE** | gross Sharpe > 0 in both windows but net ≤ 0 in either |
| **NO SIGNAL** | gross Sharpe ≤ 0 in either window |
| **INSUFFICIENT DATA** | fewer than 8 eligible symbols on more than 20% of rebalance dates |

Nothing here can reach ROBUST or VALIDATED EDGE on a backtest alone. As with
H-Scalp-3, confirmation would require a forward paper arm pre-registered before
it starts, under a stopping rule frozen across the family — the error recorded
in `docs/v3_stopping_rule.md`.

## What would falsify the hypothesis

Gross Sharpe at or below zero in either window at the primary cell, or a
universe too thin to populate terciles on a fifth of the rebalance dates. Both
are checked and reported before any cost is applied.

<!-- FROZEN ABOVE THIS LINE -->

SHA-256 of everything above the marker, computed at freeze time (before any bar was pulled):

    128059cf84dbddef0786d54f8146f857e6f05617e2966ac9471e9f217b460226

---

## DEVIATION — the skip-1 window was ambiguous

The primary arm was specified as "cumulative log return over 14 days, skipping
the most recent day (`d−15 → d−1`)". The parenthetical reads as the *no-skip*
window; the sentence around it states the intent (excluding short-term
reversal). Resolved in favour of the stated intent:

    feature(d) = log(close_{d-2} / close_{d-16})    # 14 days, skip-1  [PRIMARY]
    feature(d) = log(close_{d-1} / close_{d-15})    # 14 days, no skip

Nothing hinges on it: `skip on/off` is one of the pre-declared robustness axes,
so both readings are reported. Positions are formed from data through
`close_{d-1}` and held over day `d`, realising `log(close_d / close_{d-1})`.

## RESULT — recorded 2026-08-23, after the run

**Verdict: NO ECONOMIC EDGE.**

### The universe was better than the feasibility estimate

| | |
|---|---|
| eligible symbols, median | **27** (min 17, max 37) |
| days with fewer than 8 eligible | **0.0%** |

The pre-run figure of 16 came from *today's* turnover. The causal filter finds
**27**, because names that were liquid in 2025 and are thin now are correctly
included then. That is the survivorship correction working in the direction it
was designed to work, and it is the first thing in this document to come out
better than predicted.

### Primary cell — 14d, terciles, skip-1, $250k floor

| window | days | avg n | turnover | gross Sharpe | net Sharpe | net mean | t |
|---|---|---|---|---|---|---|---|
| train | 181 | 14.8 | 0.42 | **+0.18** | −0.25 | −0.00020 | −0.19 |
| valid | 184 | 18.3 | 0.36 | **+1.72** | +1.30 | +0.00089 | +0.89 |

Gross is positive in both windows, so this is not NO SIGNAL. Net is negative in
train, so the rule's PROMISING condition is not met. **Test stays locked.**

### Friction is genuinely no longer the binding constraint

This is the one hypothesis in the document that held. Daily L1 turnover is
~0.4, costing `0.4 × 0.079% ≈ 0.033%/day`. That converts a validation gross
Sharpe of 1.72 into a net of 1.30 — **a drag of ~0.42 Sharpe, not an
annihilation.** Every prior experiment in this program lost its entire edge to
friction; at a daily horizon the arithmetic finally works as intended. The edge
simply was not there to protect.

### The real finding is a regime split, not a cell result

Across the 24-cell robustness grid, **train net Sharpe is negative in 22 of 24
cells** while **validation net Sharpe is positive in 15 of 24**. The split is
systematic across every lookback, bucket count, skip setting and liquidity
floor — it is not a property of any parameter.

That is the same shape H-Scalp-3 produced hours earlier (`rho(gross, horizon)`
= −1.000 on train, +0.400 on validation): **a strategy family that loses in
2025H1 and wins in 2025H2.** Two unrelated hypotheses showing the same
window-dependence is evidence about the market regime, not about either
strategy. Cross-sectional momentum is known to require dispersion; 2025H1
apparently did not supply it.

### Secondary arm — 1-day reversal (pre-declared, not primary)

| window | turnover | gross Sharpe | net Sharpe |
|---|---|---|---|
| train | 1.36 | **+1.44** | +0.11 |
| valid | 1.34 | **+0.81** | −0.58 |

Reversal has *more consistent gross* than momentum — positive in both windows
where momentum was +0.18/+1.72 — but its turnover is **3.8×** higher and costs
take all of it. It does not rescue the experiment and is not promoted; it is
recorded because it is the second mechanism in this program with a positive
gross in both windows, and the first whose cost problem has an obvious lever
(passive entries) that was not available to H-Scalp-2.
