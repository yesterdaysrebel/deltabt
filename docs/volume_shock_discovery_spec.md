# Volume-shock discovery gate — frozen specification

**Written 2026-08-28, BEFORE any result was computed.** Everything below was
fixed in advance. Anything changed after a result is seen is a deviation and
must be recorded as one.

This is a **DISCOVERY** gate. Nothing here may be promoted to a validated
finding; that requires a separate freeze and an untouched validation window.

---

## 0. Prior-definition search

**NO PRIOR FROZEN DEFINITION FOUND.** `volume_shock`, `f1_volume`, `rvol`,
`abnormal volume` and `volume z-score` return zero matches across all 24
branches, every commit message, and `out/experiments.jsonl`.

One non-authoritative prior usage exists and is deliberately **not** adopted:

* `H-Compress-1` / `H-Compress-1-rev2` use `volume >= 1.5x 20-bar average` as
  one conjunct of a multi-condition breakout entry. It was never evaluated in
  isolation, 1.5x is a mild confirmation filter rather than a shock, and the
  experiment's `NO SIGNAL` verdict was of the compression breakout, not of the
  volume term. Importing a threshold from a rejected strategy would be
  adopting a definition with no provenance.

Commit `437dddb` ("Forty-seven ways to filter a 1m bar") states explicitly
that its factorial says *"nothing about filters outside this set (EMA, VWAP,
**volume**, order flow, session, market structure)"*. Volume is named there as
untested. This gate is the first test of it.

## 1. Data

`data/candles/<SYM>/ltp_1m.parquet`, single provenance:
`api.india.delta.exchange /v2/history/candles`, `resolution=1m`. `time` is the
bar OPEN in unix seconds UTC. `volume` is CONTRACTS traded in the bar.

| role | symbols | window |
|---|---|---|
| PRIMARY | BTCUSD, ETHUSD | 2025-01-01 → 2026-08-12 (588.5 d) |
| PRE-SPECIFIED robustness | SOLUSD, XRPUSD | same |
| EXPLORATORY | BEATUSD | 2026-01-05 → 2026-08-12 (218.9 d) |
| EXCLUDED, insufficient history | AKEUSD, BANKUSD (20.9 d), DOGEUSD (1.0 d) | — |

Symbols are reported separately. Nothing is pooled.

## 2. Candidate shock constructions

Two, both computed from a strictly trailing window ending at `t-1`. Neither
uses `volume(t)` in its own baseline, and neither uses price.

**C1 — `rvol_median`** (primary)

    rvol_median(t) = volume(t) / median(volume[t-L .. t-1])

The practitioner's relative volume. Median rather than mean so one earlier
spike cannot inflate the denominator and mask the next one.

**C2 — `logvol_z`** (pre-specified secondary)

    logvol_z(t) = (log1p(volume(t)) - mean(log1p(volume[t-L .. t-1])))
                  / std(log1p(volume[t-L .. t-1]))

Bar volume is strongly right-skewed and approximately log-normal, so a
z-score on logs is the natural standardised measure and adapts to regime
shifts in the volume level itself.

**Lookback `L = 1440` bars (24 hours).** Declared, not tuned.

## 3. Thresholds — declared, not tuned

    C1 shock:  rvol_median >= 5.0
    C2 shock:  logvol_z    >= 4.0

## 4. Validity at `t`

An observation is usable only if all hold:

* the trailing window contains **1440 actual bars at consecutive one-minute
  timestamps** ending at `t-60s` — a gap invalidates the window rather than
  being spanned;
* trailing median volume `> 0` (C1) / trailing std `> 0` (C2);
* the outcome bar exists at **exactly** `t + h*60`.

## 5. Episode de-duplication

After an event at `t`, no further event is recorded until `t + 60 min`. Equal
to the longest horizon, so the outcome windows of two distinct events never
overlap and one price move is never counted twice.

## 6. Outcome

    r(t,h) = | log( close(t+h) / close(t) ) |     h in {15, 30, 60} minutes

**PRIMARY HORIZON: 30 minutes.** Chosen as the middle of the declared set --
long enough that one-minute microstructure noise does not dominate, short
enough that a shock's information should still be present.

**PRE-SPECIFIED SECONDARY endpoint — volatility-normalised:**

    rn(t,h) = r(t,h) / ( sigma_trailing(t) * sqrt(h) )

where `sigma_trailing(t)` is the standard deviation of one-minute log returns
over the same trailing 1440 bars ending `t-1`. This separates *"a shock
predicts movement"* from *"shocks happen when volatility is already high"*,
which is the obvious confound and the one that decides whether the phenomenon
is anything more than volatility clustering.

## 7. Baseline

Bars with a complete trailing window and a complete outcome window that are
**not** events and are **not within 60 minutes after** any event. Post-event
bars are excluded from the baseline because leaving them in would inflate the
comparison denominator with the very effect under test.

## 8. Primary statistic

Effect: **ratio of median `r` (shock / baseline)**. Medians because bar-level
absolute returns are heavy-tailed and a mean ratio is decided by a handful of
observations.

* p-value: **circular block permutation test** -- event labels shuffled in
  contiguous 60-minute blocks, 10,000 permutations, ratio recomputed each
  time. Distribution-free and respects serial dependence.
* CI: **stationary block bootstrap**, mean block 60 minutes, 10,000 resamples.
* Mean ratio and quantiles reported alongside, never instead.

No normality assumption is used anywhere.

## 9. Multiplicity

| tier | tests |
|---|---|
| **PRIMARY** | C1, h=30m, BTCUSD and ETHUSD — **2 tests** |
| PRE-SPECIFIED secondary | C2; h in {15, 60}; volatility-normalised endpoint; SOLUSD, XRPUSD; three chronological thirds |
| EXPLORATORY | intensity buckets `rvol` in [5,10) / [10,20) / >=20; BEATUSD |

The full tested set is reported. The best cell is never presented as if it had
been pre-specified.

## 10. Decision rule, fixed in advance

**GO** requires all of:

1. primary ratio `> 1.25` on **both** BTCUSD and ETHUSD at h=30m;
2. bootstrap CI excluding 1.0 on both;
3. permutation p `< 0.01` on both;
4. the **volatility-normalised** ratio also `> 1.10` on both -- otherwise the
   effect is volatility clustering wearing a volume label;
5. the same sign and direction in **all three** chronological thirds on both.

**KILL** if the primary ratio is `<= 1.0` on either primary symbol, or the
normalised ratio is `<= 1.0` on both.

Anything else is **INCONCLUSIVE**.

## 11. Out of scope

No P&L, no Sharpe, no execution, no costs, no options data, no strategy. This
gate ends at *predictive / associational* or *no evidence*.
