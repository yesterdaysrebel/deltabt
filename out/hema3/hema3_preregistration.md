# H-EMA-3 — Pre-registration

**Status: FROZEN.** Written before any H-EMA-3 result was computed. The SHA-256
in `config.json` is computed from this file AFTER it was final — H-EMA-2's seal
was broken by appending a section post-hash, and that failure is not repeated.

---

## 1. Question

Do EMA-derived mechanisms carry directional information, and is any such
information large enough to pay Delta's round-trip cost?

H-EMA-2 answered "no edge" but could not support that claim: its per-arm minimum
detectable effect was 0.14–0.16 R while the effects it ruled out were
0.006–0.022 R — an instrument 7–24x too blunt. Independent review then found a
directional edge of +0.019 R at a 1R barrier (t ≈ 6) that H-EMA-2's fixed 2R
exit was structurally unable to see. H-EMA-3 re-asks the question with an
estimator built for it.

The pre-registered outcome set includes "an edge exists and is far too small to
trade", which is the current expectation and would be a successful result.

## 2. Why the estimator changes

H-EMA-2 measured a signal against a resampled random-direction control, arm by
arm, through a simulator that holds one position at a time. That design carried
four defects that are structural rather than incidental:

1. **Chaining destroyed the sample.** 52% of eligible setups never traded, and
   not neutrally — discarded setups had HIGHER P(2R) than kept ones (0.3531 vs
   0.3231 on one measured arm), and chaining flipped the sign of gross
   expectancy in 10 of 84 arms.
2. **The control needed matching, and matching is fragile.** Width deciles,
   seeds, shortfalls and collisions all had to be right; one open bin inverted
   the primary metric's sign.
3. **The fixed 2R exit collapsed everything to one Bernoulli parameter.**
   `gross = 3·P(2R) − 1` held to 5e-16 across all 135 arms, so the study had no
   visibility into the return distribution's shape.
4. **Per-arm inference wasted the power that pooling provides.**

## 3. The estimator (PRIMARY)

**Paired mirror-direction barrier test.** At every signal bar, compute the
outcome of BOTH directions, and score the signal against the average of the two.

    for a bet at bar b with the arm's direction d:
        out_L(k) = 1 if a LONG  reaches +k·R_L before −1·R_L, else 0
        out_S(k) = 1 if a SHORT reaches +k·R_S before −1·R_S, else 0
        stat(k)  = out_d(k) − ( out_L(k) + out_S(k) ) / 2   ∈ [−0.5, +0.5]

    excess_p(k)     = mean( stat(k) )
    excess_gross(k) = (1 + k) · excess_p(k)      R per trade

`R_L = entry − stop_long` and `R_S = stop_short − entry` are each direction's
OWN frozen structural stop, so neither side is handicapped.

Why this is the right instrument:

- **Scale-free under the null.** For a martingale, P(hit +kR before −1R) =
  1/(1+k) for ANY stop width. The long/short stop-width asymmetry that
  contaminated H-EMA-2's control therefore cannot bias it.
- **Exactly paired.** Both directions are evaluated on the same bar, so
  bar-selection effects, volatility regime and drift-in-the-window cancel.
- **No control to construct.** No deciles, no seeds, no shortfalls, no
  collisions, no resampling noise.
- **No chaining.** Every signal is evaluated; nothing is discarded for being
  concurrent with another position.
- **Sees the whole barrier curve.** Sweeping k exposes where information lives
  and where it reverses, which a single fixed target cannot.

## 4. Barrier multiples

    k ∈ {0.5, 1, 2, 4}

Fixed here. k = 2 reproduces H-EMA-2's exit and is the comparability anchor.
No other k may be added afterwards.

## 5. Universe, data, splits, stops, costs

Unchanged and inherited:

- Universe BTCUSD ETHUSD SOLUSD XRPUSD; BEATUSD supplementary on VALID only;
  AKEUSD/BANKUSD excluded (TEST-window-only history).
- TRAIN 2025-01-01 → 2025-12-20. VALID → 2026-04-16. TEST locked.
- Stop: frozen Supertrend(10, 2.0) leg-extreme on the execution timeframe.
- Signals on 5m/15m/1h; barriers resolved on the 1m grid.
- Costs: per-symbol taker ×1.18 GST + 2.0 bps slippage, per-symbol funding.
- Mechanisms M1–M5 and the five EMA pairs are IDENTICAL to H-EMA-2's frozen
  manifest, so H-EMA-3 changes the measurement and nothing else.

## 6. Boundary handling

The forward walk is TRUNCATED at the split boundary. A bet that has reached
neither +kR nor −1R by then is UNRESOLVED at that k. Unresolved bets are
excluded from the primary statistic and their count reported; a declared
sensitivity re-runs the statistic scoring unresolved bets as misses. This
removes H-EMA-2's D6 defect, where walks resolved on the next segment's data.

Same-bar convention is the frozen one: a bar touching both the barrier and the
stop resolves to the STOP, so the bar's favourable excursion does not count.

## 7. The 5% stop cap

Reported BOTH ways, because it is a tradeability constraint rather than a
measurement one, and review showed it removes the WORSE setups (P(2R) 0.314 vs
0.357 at 1h) so it flatters rather than truncates:

- PRIMARY: no cap — every signal is measured.
- SECONDARY: capped subset, for comparability with H-EMA-2.

## 8. Deduplication

Arms share signal bars — M2 at threshold 0.00 largely reproduces M1. Counting a
bet once per arm would inflate n without adding information. The pooled
statistic is computed over bets DEDUPLICATED on
`(symbol, exec_tf, bar_index, side)`. Per-arm figures are reported separately
and are descriptive.

## 9. Inference

Cluster-robust standard errors, clustering on **symbol-day**, applied to the
paired statistic:

    Var(mean) = (1/n²) · Σ_g ( Σ_{i∈g} (stat_i − mean) )²

Reported for every figure: n, number of clusters, mean, cluster-robust SE, t,
and a 95% interval. No result is quoted without an interval. The minimum
detectable effect of the design is stated in the report alongside the estimate,
so a null can never again be claimed by an instrument too blunt to see it.

## 10. The economic gate

Directional information is only tradeable if it exceeds the round-trip cost:

    round trip = 2 × (effective_taker + slippage) = 15.8 bps of price
    cost in R  = round trip / stop_pct        (measured: 0.077–0.305 R)

An arm is tradeable only if `excess_gross(k) > cost/R` at the same k and
timeframe. Reporting `excess_gross` without that comparison is forbidden.

## 11. Gates

**PROMISING** requires ALL of:
1. `excess_gross(k) > 0` with a 95% interval excluding zero, on TRAIN and VALID;
2. `excess_gross(k)` exceeding the measured cost/R at that timeframe;
3. consistent sign across all four symbols;
4. consistent sign across both halves of TRAIN;
5. replication on VALID at the same k with the same sign and comparable size.

**INCONCLUSIVE** if the interval spans zero or the design lacks the power to
distinguish the estimate from zero.

**DEAD** if the information is real but below the cost floor, or absent. A
statistically significant edge smaller than the round trip is DEAD as a trading
hypothesis and must be labelled so, however large its t-statistic.

## 12. Multiplicity

4 barrier multiples × 5 mechanisms × 3 timeframes is a small, fully-reported
grid, and the pooled primary is a single number per k. Every cell is published.
The maximum |t| expected under the null is computed by simulation on the actual
correlated bet set rather than asserted, which H-EMA-2 got wrong.

## 13. Anti-leakage

No parameter is tuned after TRAIN. No k, mechanism, timeframe or symbol is
chosen after seeing VALID. No stop or cost assumption is altered. The forward
walk reads only bars at or after the entry bar. VALID is run once, blind, after
this document and the manifest are on disk and the implementation's tests pass.

## 14. Deviations

None. Any deviation found later is recorded here with the date it was found and
whether it preceded or followed the run that it affects.
