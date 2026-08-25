# Backtest sweep — 2026-08-25

576 cells: 8 symbols x 12 strategy families x 6 timeframes. 492 ran; 84 were
too short (DOGEUSD has 1,435 cached 1m bars, AKEUSD/BANKUSD ~21 days). 349
cells cleared 30 trades and are pooled below; 218,171 trades in total.

Reproduce:

    PYTHONPATH=. python3 -u scripts/backtest_sweep.py
    PYTHONPATH=. python3 scripts/sweep_report.py
    PYTHONPATH=. python3 -u scripts/walkforward_specs.py --splits 4

`backtests.csv` is one row per cell, `report.txt` the pooled tables.

Every cell is a `deltabt.spec.StrategySpec` run through `deltabt.rulecore` and
`deltabt.engine.run_backtest` -- the same definition the paper trader executes,
so a cell worth forward-testing is promotable without being rewritten.

## What the grid holds constant

Comparing timeframes only means something if the non-strategy settings do not
move with the timeframe:

- `max_hold_bars` is scaled to a constant **48 hours**. The default 240 bars is
  4 hours at 1m and 40 days at 240m.
- Confirmation is a constant **5:1** ratio to the primary, not pinned at 1m
  where a 240m primary would be confirmed by noise.
- The cost gate stays at `max_cost_per_r = 0.15`. Its rejection count is
  reported per cell rather than tuned away.

## Headline

| primary | win rate | gross R | net R | % of signals refused on cost |
|---|---|---|---|---|
| 1m | 24.7% | -0.027 | **-0.134** | 97.5% |
| 5m | 29.4% | -0.014 | -0.116 | 91.5% |
| 15m | 32.5% | +0.024 | -0.073 | 77.3% |
| 30m | 33.3% | +0.039 | -0.051 | 55.4% |
| 60m | 33.5% | +0.028 | -0.049 | 23.7% |
| 240m | 39.0% | +0.059 | **+0.010** | 0.5% |

Monotone in the timeframe on every column, across twelve unrelated rule sets.
240m is the only timeframe with a positive pooled net.

**Read the rejection column, not `cost_r` on taken trades.** The gate truncates
`cost_r` from above, so realised cost on executed trades is compressed toward
0.15 at every timeframe and understates the spread between them.

## Caveats that bound what this shows

- **43 of 349 cells (12.3%) have positive net expectancy. Exactly one has a
  bootstrap CI excluding zero** -- BEATUSD / wpr_only / 30m at +0.293R on 71
  trades. At 349 cells and a nominal 5% bar, ~17 cells would clear by chance,
  so one is fewer than chance would produce.
- Only `trend_wide_stop` is net-positive on a majority of symbols, at 60m and
  240m (3 of 4 each). Every other family/timeframe fails that test.
- The 240m row rests on 2,199 trades across 4 symbols. It is the thinnest row
  in the table and the one carrying the positive result.
- Win rate at 1m and 5m sits **below** the 33.3% random-entry rate for a 2R
  target, converging on it from below as the timeframe widens -- the signature
  of stops sitting inside the noise rather than of entries carrying direction.


---

# Walk-forward — the grid does not survive out of sample

Everything above is an IN-SAMPLE fit: every cell saw every bar.
`scripts/walkforward_specs.py` runs four anchored splits over BTC/ETH/SOL/XRP,
selecting on the training window and measuring on the block that follows.

## 1. Selection: choose on the past, measure on the future

| split | train to | test to | selected | in-sample net R | OOS net R |
|---|---|---|---|---|---|
| 0 | 2025-04-28 | 2025-08-24 | wpr_only@240m | +0.0952 | +0.0451 |
| 1 | 2025-08-24 | 2025-12-20 | wpr_only@240m | +0.0727 | +0.0971 |
| 2 | 2025-12-20 | 2026-04-16 | trend_wide_stop@240m | +0.1002 | **-0.1380** |
| 3 | 2026-04-16 | 2026-08-12 | adx_only@240m | +0.1058 | **-0.1545** |

    mean in-sample net R of the picks : +0.0935
    mean out-of-sample net R          : -0.0376
    selection premium                 : +0.1310
    splits positive out of sample     : 2 of 4

**The selection premium is larger than the in-sample edge it explains.** A cell
chosen on the training window carries +0.0935R in-sample and -0.0376R on the
data it did not see; the whole apparent edge, and then some, was the act of
choosing among ~70 candidates.

## 2. Fixed cells: the in-sample survivors, block by block

Net R for the same named cells in every block, ranked or not:

| cell | 0 | 1 | 2 | 3 | symbols positive (per block) |
|---|---|---|---|---|---|
| adx_only@240m | +0.104 | +0.214 | +0.072 | **-0.155** | 3/4, 2/4, 2/4, 0/4 |
| hwpr_no_confirm@240m | -0.195 | +0.256 | -0.206 | -0.236 | 1/4, 3/4, 1/4, 1/4 |
| st_only@240m | -0.092 | +0.404 | +0.212 | -0.090 | 1/4, 4/4, 3/4, 0/4 |
| trend_wide_stop@240m | +0.076 | +0.269 | -0.138 | -0.049 | 1/4, 4/4, 0/4, 1/4 |
| trend_wide_stop@60m | +0.026 | +0.038 | +0.113 | -0.058 | 3/4, 3/4, 4/4, 1/4 |

**Every cell changes sign across blocks, and all five are negative in the most
recent one.** `trend_wide_stop@240m` runs 1/4 -> 4/4 -> 0/4 -> 1/4 on
symbols-positive: not a mechanism weakening, a number with no stable sign.

`st_only@240m` at +0.404 in block 1 is the shape to be most suspicious of --
a large isolated block on 43 trades, adjacent to -0.092 and -0.090.

## 3. Gross R per block -- attribution, with cost removed entirely

Net conflates two different failures. Charging no cost at all separates them:
a negative GROSS is an absent signal, which no cost saving can rescue; a
positive gross with a negative net is friction.

| cell | 0 | 1 | 2 | 3 | gross sign stable? |
|---|---|---|---|---|---|
| adx_only@240m | +0.152 | +0.264 | +0.120 | -0.101 | no |
| hwpr_no_confirm@240m | -0.153 | +0.302 | -0.164 | -0.188 | no |
| st_only@240m | -0.041 | +0.450 | +0.257 | -0.035 | no |
| trend_wide_stop@240m | +0.100 | +0.293 | -0.115 | -0.022 | no |
| **trend_wide_stop@60m** | **+0.073** | **+0.084** | **+0.161** | **+0.002** | **yes, 4/4** |

The selection test agrees: splits 2 and 3 have out-of-sample GROSS of -0.115
and -0.101 -- negative before a single basis point is charged.

**Four of the five survivors fail for lack of signal, not for cost.** Their
gross changes sign between adjacent blocks. Setting friction to zero does not
rescue any of them, so "reduce costs" is not a route to making them work.

## Verdict

The 240m cells at the top of the in-sample table are **selection, not edge**:
their gross flips sign block to block, and the selection premium (+0.1310R)
exceeds the in-sample edge it explains.

**One cell is a genuine friction case and is not covered by that verdict.**
`trend_wide_stop@60m` is gross-positive in all four blocks and net-positive in
three, averaging +0.0798 gross against +0.0297 net -- cost consumes **63%** of
its gross. It runs at **1.76 trades/day across four symbols**. It is the only
cell in the grid where lowering friction would change the answer rather than
merely reduce the loss.

It is still a survivor of a 349-cell in-sample grid, and its most recent block
is its worst (gross +0.002, net -0.058). It is a candidate for closer
examination, not a validated edge.

The sweep's most durable output remains the cost-wall picture, which is
monotone, reproducible, and consistent across twelve unrelated rule sets.

---

# Addendum, 2026-08-25 — the 4/4 gross sign is not rare

Everything above tests sign stability on **seven** cells, and every one of them
is in that list because it ranked well in sample. So "gross sign stable? yes,
4/4" was measured on a set already filtered by the effect it was meant to
detect, and the count had nothing to be compared against.

`scripts/walkforward_specs.py --fixed all` tracks all **72** family x timeframe
cells through the same four blocks.

    cells tracked                    : 72
    cells positive in all 4 blocks   :  6
    expected by chance at p=0.5      :  4.50
    ratio observed/expected          :  1.33x
    P(>= 6 of 72 | chance)           :  0.294

The whole distribution is the binomial:

| blocks held | observed | expected |
|---|---|---|
| 0/4 | 6 | 4.5 |
| 1/4 | 16 | 18.0 |
| 2/4 | 25 | 27.0 |
| 3/4 | 19 | 18.0 |
| 4/4 | **6** | **4.5** |

**Holding the gross sign through every out-of-sample block is what chance does
with this many cells.** It is not a property that distinguishes a cell, and it
should not have been used as a selection criterion.

The six that hold every block:

| cell | gross, blocks 0-3 | net, blocks 0-3 |
|---|---|---|
| hwpr_no_confirm@15m | +.099 +.005 +.030 +.001 | +.007 −.084 −.059 −.098 |
| st_only@1m | +.087 +.043 +.045 +.005 | −.037 −.078 −.076 −.117 |
| st_only@30m | +.024 +.006 +.131 +.031 | −.068 −.082 +.045 −.068 |
| trend_pure@15m | +.029 +.009 +.011 +.007 | −.059 −.075 −.074 −.090 |
| trend_wide_stop@60m | +.073 +.084 +.161 +.002 | +.026 +.038 +.113 −.058 |
| wpr_only@240m | +.096 +.146 +.042 +.023 | +.045 +.097 −.004 −.034 |

**All six are net-negative in the most recent block.** Four are net-negative in
every block: their positive gross never survives contact with cost, so they are
not friction cases and lowering fees would not reach them.

## What this changes above

The Verdict section calls `trend_wide_stop@60m` "the only cell in the grid
where lowering friction would change the answer". Two corrections:

1. It is not the only one — it was the only one **among the seven that had been
   looked at**. `wpr_only@240m` behaves the same way and was found by the same
   filter.
2. Neither is distinguished by holding 4/4. Six cells do, against 4.5 expected.
   Whatever case exists for either rests on gross magnitude and on cost being
   the binding constraint, **not** on sign persistence.

`wpr_only@240m` was selected for the v5 paper trade partly on this criterion.
It has the largest gross magnitudes of the six and the gentlest decay, which is
the strongest statement the data supports, and it is not a strong one. See
`docs/v5_stopping_rule.md`.
