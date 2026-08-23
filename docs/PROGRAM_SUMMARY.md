# deltabt — closing summary

**Written 2026-08-23, when the program stopped.**

Thirteen pre-registered hypothesis tests plus one diagnostic. **No edge was
found.** This document records what was established, what was ruled out, and
what a future attempt should not repeat.

The nulls are the deliverable. A registry that only kept the winners would be
worth nothing; `out/experiments.jsonl` kept all of them, and it is the reason
the negative result can be trusted.

---

## What was established

### 1. The cost law

    cost_r = round_trip_rate / stop_pct

Cost per unit of risk is set by **how far away your stop is**, and by
essentially nothing else. Verified to four decimal places across an 11× range
of stop widths, five timeframes, and both intraday and daily horizons. On Delta
India the round trip is a constant `0.158%` for every crypto perp — twice the
taker fee (0.05% × 1.18 GST) plus 2 bps/side slippage.

The consequence is arithmetic and inescapable:

| stop width | cost per R | break-even win rate at 2R |
|---|---|---|
| 0.3% | 0.527 | 50.9% |
| 0.6% | 0.263 | 42.1% |
| 1.5% | 0.105 | 36.8% |
| 4.3% | 0.037 | 34.6% |

A random entry at a 2R target wins `1/(1+R)` = 33.3%. **A 1-minute strategy on
this venue must beat ~42% before it earns anything.** That is the wall
everything intraday died against.

### 2. Where the cost wall ends

Friction consumed 54–136% of gross P&L at intraday horizons. At a **daily**
horizon it took 0.42 of a 1.72 Sharpe — a drag, not an annihilation. The wall
is a function of holding period and it stops binding somewhere around a day.
Any future attempt on this venue should start there.

### 3. No symbol on the venue fixes it

All 220 listed perpetuals were measured for cost/R, liquidity and quoted
spread. **Zero of 34 cheap-tier candidates beat the incumbents.** The
tokenised-equity tier is 45% cheaper in fees and has ~100× less 1-minute
volatility, giving a median cost/R of **5.7** against BEATUSD's **0.105**;
`SPYXUSD` would need a 1295% win rate. Low fees do not help when volatility
falls faster than the fee does.

A related trap: **14 of 40 symbols quote a half-spread wider than the 2 bps the
cost model assumes.** BEATUSD's real cost/R is 0.180, not 0.105.

### 4. Market structure genuinely changed

Average pairwise correlation among liquid perps roughly halved and
cross-sectional dispersion doubled between mid-2024 and mid-2026:

| window | pairwise corr | dispersion |
|---|---|---|
| 2024H2 | 0.602 | 0.0331 |
| 2025H1 | 0.678 | 0.0360 |
| 2025H2 | 0.655 | 0.0386 |
| 2026H1 | 0.451 | 0.0584 |
| 2026H2 | 0.339 | 0.0668 |

True, and it did **not** rescue anything — see below.

---

## What was ruled out

Thirteen tests. **Ten were negative before costs**, meaning no mechanism at
all. Three had positive gross.

| # | family | verdict |
|---|---|---|
| 1–2 | short-horizon mean reversion (15m) | NO EDGE |
| 3 | short-horizon continuation / retest (H-Scalp-2, 15m) | NO ECONOMIC EDGE |
| 4–5 | volatility regime transition / breakout-retest | NO SIGNAL |
| 6 | funding / leveraged crowding | NO SIGNAL |
| 7 | relative value / pair | NO SIGNAL |
| 8–9 | multi-timeframe trend continuation | NO ECONOMIC EDGE / NO SIGNAL |
| 10 | H-WPR-1 ATR arm, live paper (v3) | UNDECIDED, n=31 |
| 11 | reversal confluence, live paper (v4) | null, n=45, t=+0.08 |
| 12 | H-Scalp-3 — continuation at longer horizons | NO SIGNAL |
| 13 | H-XSec-1 — cross-sectional momentum, daily | NO ECONOMIC EDGE |

**The unifying result.** Every directional test landed on a win rate of
`1/(1+R)` — the exact random-entry rate. Across 16 separate measurements the
observed rate matched within about one point. Widening stops from 0.75× to 8×
ATR moved the win rate 0.187 → 0.340, converging on 33.3% **from below**: tight
stops are *worse* than random because the stop sits inside the noise, and
widening removes an artifact rather than adding an edge.

**Excursions confirm it a third way.** v3's losing trades' forward runs match a
driftless random walk almost exactly (72% reach +0.25R against 70% predicted;
18% reach +1R against 25%). Losers' mean favourable excursion (+0.557R) is
indistinguishable from winners' mean adverse excursion (0.493R) — in flight,
winners and losers look the same. A path-ordered breakeven-stop replay cost
**−7.55R**: it rescued 3 losers and killed 6 winners.

**The one mechanism that was real.** H-Scalp-2 had gross `+0.1156R`, positive
in train, validation *and* test, and on all four symbols. It lost `0.0203R` to
a cost/R of `0.1359` — friction, not signal. H-Scalp-3 extended it to longer
horizons where cost/R falls as `1/sqrt(T)`. Cost fell exactly as predicted
(ratios 0.82–1.00), and the gross did not survive: `rho(gross, horizon)` =
**−1.000** on train against **+0.400** on validation. A real mechanism does not
reverse its horizon dependence between adjacent half-years.

**The regime excuse was tested and rejected.** Two unrelated hypotheses split
the same way across 2025H1/H2, so `run_regime.py` measured whether the train
window was structurally hostile. A criterion fixed in advance returned
PATHOLOGICAL — **and the criterion was wrong**: it tested rank rather than
magnitude, and 2025H1 leads 2025H2 by 0.678 vs 0.655 on correlation and 0.803
vs 0.799 on vol. The decisive measurement is the information coefficient, which
isolates signal from portfolio construction, breadth and cost:

| window | IC (14d) | t |
|---|---|---|
| 2024H2 | −0.0142 | −0.49 |
| 2025H1 | −0.0273 | −1.15 |
| 2025H2 | +0.0155 | +0.76 |
| 2026H1 | −0.0041 | −0.20 |
| 2026H2 | +0.0162 | +0.43 |

Zero everywhere. And **2026H1/H2 — lowest correlation, highest dispersion, the
most favourable regime a cross-sectional signal could ask for — give −0.0041
and +0.0162.** If regime were the explanation those would be strongly positive.

---

## What a future attempt should not repeat

1. **Do not test another indicator combination on 1m/5m crypto perps.** 213
   configurations, two live arms and a 0.75×–8× ATR sweep say the same thing.
   The cost wall alone requires a 42% win rate before anything is earned.
2. **Do not try to fix cost by picking symbols.** All 220 were measured. Cheap
   fees come with low volatility; low cost/R comes with wide spreads.
3. **Do not use circuit breakers while measuring.** They censor the sample —
   the gated mean is expectancy *conditional on the day not having gone badly*,
   which is larger by construction. Measured on v3: gated and ungated friction
   were 0.239R and 0.241R, identical, while the standard error rose from 0.272
   to 0.335. Gating cost information and changed no conclusion.
4. **Do not act on in-flight information.** Breakeven stops, trailing stops and
   partial take-profits all key off an excursion signal that does not exist.
5. **Do not freeze a stopping rule per-arm after an arm is already running.**
   `docs/v3_stopping_rule.md` was frozen and abandoned the same day. Freeze
   across the family before any arm starts.
6. **Do not trust a validation column alone.** H-Scalp-3's 120m cell showed
   `t = 2.339`, a confidence interval excluding zero, and survival of a 1.5×
   cost stress — on one cell out of 360, with train at −0.0259. It was noise.
7. **Do not filter a backtest universe on today's liquidity.** That selects
   what survived. The causal filter in `run_hxsec1.py` found a median of 27
   eligible symbols where current turnover suggested 16.

---

## What the apparatus caught

Recorded because it is the argument for the overhead:

- **H-Scalp-3's 120m cell** — significant `t`, clean CI, survived cost stress,
  one cell of 360. Rejected by a rule frozen before the run.
- **H-XSec-1's validation column** — net Sharpe 1.30. Rejected because train
  was −0.25 and the rule required both.
- **Its own author's criterion** — `run_regime.py` returned a verdict that
  would have reopened the whole program. Rejected on the IC evidence, and the
  criterion left unedited in the file with a note saying it is wrong, because
  retuning it after seeing the answer is the failure it existed to prevent.

Also caught along the way: a same-bar target look-ahead bug in H-Scalp-2 that
had shown `+0.482R` at `t = 32` before the fix, and an equity-ruin artifact
where a bankrupt train account was carried into validation, collapsing 8,892
trades to 179.

---

## Infrastructure at close

Both paper arms stopped with recorded reasons; three positions left open rather
than fabricating exits. Both EC2 instances terminated, EIPs released, log groups
and per-stack alarms destroyed.

**Retained:** RDS `deltabt-paper` (~$15/month, holding `deltabt_v3` and
`deltabt_v4`), manual snapshot `deltabt-paper-final-v3-v4-20260823t185433z`
which survives instance deletion, the VPC, IAM roles including GitHub OIDC, and
the ECR repository.

To tear the rest down, the database's `prevent_destroy = true` in
`infra/terraform/rds.tf` must be removed deliberately — it is a last line of
defence, not an obstacle. The snapshot makes that safe whenever it is wanted.
