# m3 forward test — evaluation stopping rule

**Frozen 2026-09-13, before the experiment exists.** The universe change that
starts this run (`bot_symbols` → `BEATUSD,ETHUSD,SOLUSD`) is committed in the
same change as this document, ahead of the apply that registers the successor
experiment. Written while `n = 0`, so nothing in it can have been chosen to
fit an observed result. Any deviation is documented as a deviation, not
silently absorbed.

This exists because the arm it replaces ran without one — flagged as F5 in
`reports/both_t3_day7_review.md` — and because three arms have now been ended
early against or without their rules (the 1h-direction arm 2026-09-04,
`hours` and `cross` 2026-09-10). PROGRAM_SUMMARY lesson 5 says freeze before
the first bar; this is that, for this run.

## What is under test

| | |
|---|---|
| stack | `atr` (`deltabt-paper-atr`, database `deltabt_m3`) |
| strategy | `SPEC:manual_scalp_both_t3@5` — %R(140) variant_a on the 5m AND the 1m, no Supertrend/DI/ADX, 4×ATR(10) stop, 3R target |
| strategy hash | `41e764beceaf787f4b54ec25106b4c366e375478f4dbf3660d1a3b20c686f88d` (unchanged from the thin-universe run) |
| risk hash | `cbd18caa5378e90d` (unchanged: 0.5% risk, 6 slots, 72h max hold, breakers off) |
| symbols | BEATUSD, ETHUSD, SOLUSD |
| by | operator instruction, 2026-09-13, with the contrary record retained in `infra/terraform/variables.tf` |

## The pre-registered expectation

**Net negative on ETHUSD and SOLUSD; the most likely verdict for the run is
that the cost law held on the majors.** Stated before the first bar so the
outcome cannot be re-read later as surprise in either direction:

- SOLUSD is the only major measured under this exact family: net −0.129R at
  the live exit, 0 of 4 anchored blocks, gross −0.035R — negative before
  fees (2026-09-04 record).
- ETHUSD is unmeasured under this cell. The cost law prices its 4×ATR 5m
  stop at roughly 0.10–0.12R per trade against 0.03–0.04 on the thin
  universe, and every portfolio measurement of this family that included
  majors was strongly negative.
- BEATUSD is the carried symbol (+0.155R over 303 backtest trades) and the
  only one with a positive prior.

If ETHUSD comes back clearly positive at review, that is the first evidence
anywhere that this entry survives major-symbol costs, and it would justify a
properly measured follow-up — not a conclusion by itself.

**Expected trade rate:** the majors generate most of the trades wherever they
have been included; expect roughly 3–6 closed/day against the thin
universe's 2.1, so the trade bound below should arrive well inside two
weeks. If the rate is materially lower than that, say so at review; do not
adjust anything mid-run to chase it.

## The rule

1. **Review at 40 closed trades or day 30, whichever comes first. Make no
   decision before that.** Daily reports before the review point are for
   execution correctness only, exactly as the sample line in them says.
2. **Stop early ONLY on cumulative −12R or a 20% drawdown from peak.**
   Nothing else — not a losing week, not a symbol at 0-for-8, not the
   equity number on any given morning. At the pre-registered expectation a
   bad first week is the forecast, not a surprise.
3. **At review the run continues only if net R > 0 AND at least 2 of the 3
   symbols are positive**, scoped by `experiment_id`, per-symbol counts
   shown. Per-symbol reads under ~10 closed trades are weather; if a symbol
   has fewer than that at review, its sign does not count toward the
   2-of-3.
4. **The comparison baseline includes live exit costs.** The day-7 review
   measured ~0.07R/trade of stop gap-through the backtest's fill model does
   not carry (F2). Live-vs-backtest divergence at review is read against a
   baseline that pays it, or it is not read at all.

## What this run cannot show

40 trades cannot establish an edge; at this family's per-trade spread
(SD ≈ 1.8R) the 95% interval on the mean at n=40 is roughly ±0.55R. The run
produces out-of-sample evidence on ETHUSD and SOLUSD that no archive
contains, and a continue/stop decision under the rule above. It does not
produce a validated strategy, and its P&L must not be read as one.
