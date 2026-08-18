# H-COST-1 — Pre-registration

**Status: FROZEN.** The SHA-256 in `candidates.json` is computed from this file
AFTER it was final. No section may be appended without re-hashing and recording
the change, which is the failure that voided H-EMA-2's seal.

---

## 1. Question

Not "which signal works". H-EMA-3 already established that EMA-derived signals
carry real directional information:

    VALID excess at k=0.5R   +0.0181 R   t = +5.40   CI [+0.0115, +0.0246]
    positive in all four core symbols, both VALID halves, and blind BEATUSD

against a round-trip cost floor of 0.297 R. H-COST-1 asks instead:

> **Is there a region of stop geometry, timeframe, volatility, symbol and
> execution cost in which that already-demonstrated edge survives?**

This is an economic feasibility experiment. The signal is frozen and is never
modified, tuned, re-selected or extended.

## 2. The frozen signal, and how it was verified

Source: `out/hema3/` at preregistration sha256
`00ffd0bcf97834dc0ae0bc7eaf6e5b977e3defe9423279d2832525bc3a46288d`, verified to
match the file. The reported VALID k=0.5 figure was recomputed from
`bets_valid.parquet` and reproduced exactly (+0.0181 R, t +5.40, n 40,555).

The H-EMA-3 result is a POOLED, DEDUPLICATED bet population over all 135 arms,
keyed `(symbol, exec_tf, bar, side)` — a measurement, not a trading rule.

### 2.1 Conflicting-bar rule (FROZEN, Decision 1)

1.5% of bars (616 of 40,780 on VALID) are signalled LONG by one arm and SHORT by
another. The executable signal is therefore:

    LONG-only bar   -> LONG
    SHORT-only bar  -> SHORT
    BOTH            -> DROP
    NONE            -> no trade

No majority vote, no first-arm-wins, no invented direction. Conflicting bars,
their percentage and the dropped observation count are reported separately, and
the executable signal is reconciled against the H-EMA-3 figure so the difference
attributable solely to dropping conflicts is documented.

## 3. Separation of the two questions

Every result reports these separately and never collapses them:

    A. SIGNAL VIABILITY    does the executable signal beat its matched control?
    B. ECONOMIC VIABILITY  does that excess survive realistic costs?

A configuration reading `SIGNAL: REAL / ECONOMICS: NOT VIABLE` is reported that
way, not as "the strategy failed".

## 4. Pre-run prediction (FALSIFIABLE, recorded before TRAIN)

> Based on the H-EMA-3 distance-decay curve, widening the stop is expected to
> reduce cost/R but simultaneously move the target farther into a region where
> measured signal edge decays or reverses. Therefore no primary configuration is
> predicted to be economically viable within the 5% stop constraint, although
> symbol/volatility/timeframe heterogeneity may produce exceptions.

The arithmetic behind it, computed before any H-COST-1 run. At the frozen median
R of 0.532% of price, cost/R = 15.8 bps / stop_width:

| stop width | cost/R | 0.5R target sits at | H-EMA-3 measured edge there |
|---|---|---|---|
| 0.50% | 0.316 | 0.250% | +0.0181 |
| 1.00% | 0.158 | 0.500% | +0.0007 |
| 2.00% | 0.079 | 1.000% | −0.0137 |
| 5.00% | 0.032 | 2.500% | extrapolated, worse |

Break-even (cost/R = 0.0181) requires a stop width of **8.73% of price**, which
is above the frozen 5% ceiling. This prediction is not modified after TRAIN.

## 5. Primary model vs diagnostic (FROZEN, Decision 2)

    Primary model:   stop width <= 5%    (frozen max_stop_pct = 0.05)
    Diagnostic:      stop width  > 5%    OUT-OF-MODEL

**Diagnostic results cannot rescue the primary experiment.** The out-of-model
band takes no part in primary gates, candidate selection, VALID selection or the
economic verdict, and may never support a claim that the frozen strategy is
viable. Its sole purpose is to locate the break-even boundary if the 5%
constraint were relaxed. A diagnostic that turns positive at 10% is reported as
"break-even appears beyond the frozen 5% risk constraint", never as "profitable
with a 10% stop".

## 6. Stop width (PRIMARY AXIS)

    PRIMARY (in-model)    0.25  0.50  0.75  1.00  1.50  2.00  3.00  5.00  %
    DIAGNOSTIC (out)      7.50  10.00                                     %

Implemented as a synthetic stop-distance layer: `stop = entry x (1 -/+ width)`,
leaving the signal untouched. This makes cost/R exact rather than distributional:
`cost/R = 2(taker + slippage) / width`.

Wider is NOT assumed better. Each cell reports stop-hit rate, target-hit rate,
holding time, gross edge, cost/R and net edge so the trade-off is measured.

## 7. Timeframe, volatility, symbols

Timeframes 5m / 15m / 1h — those already in the frozen H-EMA-3 definition. None
added.

Volatility regimes from ATR(14)/close on the execution timeframe, thresholds set
from the **TRAIN** distribution only and frozen before evaluation:

    LOW     <= P33
    NORMAL  P33 - P67
    HIGH    >  P67

Universe BTCUSD ETHUSD SOLUSD XRPUSD. BEATUSD supplementary blind OOS only; its
0.076 R cost floor against the majors' 0.297 R is the motivating contrast but is
NOT assumed to replicate. AKEUSD/BANKUSD excluded (TEST-window-only history).

## 8. Exit horizon

H-EMA-3 showed the signal decays fast, so a 2R target is not assumed. Small,
pre-declared set, no unconstrained search:

    Stage A (fixed):  k = 0.50 R
    Stage B:          k = 0.25, 0.50, 0.75, 1.00 R

2R and 4R are retained only as declared reference points, never as the sole
economic test.

## 9. Cost scenarios

    A  BASELINE     per-symbol taker x1.18 GST + 2.0 bps slippage   (frozen)
    B  LOW SLIP     1.0 bps slippage
    C  HIGH SLIP    5.0 bps slippage
    D  FEE SENS     maker-rate exit (limit target rests) + baseline slippage

Declared here, before any result. The purpose is the cost-sensitivity curve, not
to find an assumption cheap enough to produce a winner.

**Slippage is an assumption, not a measurement.** No historical order-book depth
exists in this dataset. Thin instruments are therefore NOT to be called
attractive on the strength of a flat 2 bps assumption; scenario C exists
specifically to test whether an apparent advantage survives a realistic penalty
for illiquidity. Synthetic-bar share is reported per symbol as the available
liquidity proxy.

## 10. Two-stage design (FROZEN before TRAIN)

    Stage A   timeframe x stop width x volatility, baseline cost, k = 0.50
    Stage B   exit x cost scenario, over the regions Stage A maps

Stage B evaluates the pre-declared exit and cost grid over ALL Stage A cells
meeting the minimum sample rule (n >= 500); it does not select cells by their
Stage A performance. This is stated to prevent a hidden adaptive search.

## 11. Control

The H-EMA-3 paired mirror-direction control, unchanged: at every signal bar,
score the signal against the average of both directions under the SAME synthetic
stop. Under a martingale P(hit +kR before -1R) = 1/(1+k) for any stop width, so
the control remains valid as stop width is swept — which is exactly why this
estimator, and not a resampled one, is used for an experiment whose primary axis
is stop geometry.

Reported per cell: signal gross, control gross, excess gross, signal net,
control net, excess net.

## 12. Gates

    GREEN   positive excess vs control AND positive net expectancy under the
            BASELINE cost scenario, n >= 500, no leakage
    YELLOW  positive excess, but net flips sign under scenario B or C
    RED     excess absent, or net negative under baseline

A cell is never GREEN on the strength of a cheap cost scenario, and never GREEN
from the out-of-model band.

## 13. Break-even cost/R

For every cell, report the cost/R at which net expectancy reaches zero:

    break_even_cost_R = excess_gross_R

and the ratio `required_edge / observed_edge` when a cell is RED, so the answer
is "the signal would need to be X times larger" rather than "it lost money".

## 14. Prohibited

New EMA pairs. Indicator tuning of any kind. Post-hoc regime, stop width or cost
definitions. Selecting a symbol, volatility bucket or cell after seeing VALID.
Modifying the signal to fit the economics. Modifying H-EMA-3 or its results.

## 15. Deviations

None. Any deviation found later is recorded here with the date and whether it
preceded or followed the run it affects.
