# H-Scalp-3 — Pre-registration (frozen before any result was inspected)

**Frozen 2026-08-21.** Recorded before running. Any deviation is documented as
a deviation, not silently absorbed.

## Why this hypothesis and not another

Eleven tests in this program have returned nothing: nine pre-registered offline
experiments in `out/experiments.jsonl`, plus two live paper arms stopped today
(v3 `t = +0.42` at n = 31, v4 `t = +0.08` at n = 45). Ten of the eleven are
negative **before costs**, which is the signature of no mechanism at all.

**H-Scalp-2 is the single exception**, and the only reason this experiment
exists:

| | gross | net |
|---|---|---|
| train 2025H1 | +0.1429 | +0.0341 |
| valid 2025H2 | +0.0670 | −0.0749 |
| test 2026 | +0.1295 | −0.0236 |
| **overall** | **+0.1156** | **−0.0214** |

`n = 2,466`, `n_eff = 1,676`, `cost/R = 0.1359`, classified NO ECONOMIC EDGE.
**Gross was positive in all three windows and on all four symbols
independently.** The mechanism exists. It loses by 0.0203R, entirely to
friction.

## Hypothesis

Cost per R is not a property of the strategy; it is arithmetic:

    cost_r = round_trip_rate / stop_pct

H-Scalp-2's R is `(1 − retest) × |15m move|` — small, which is why 0.1359R of
cost consumes a 0.1156R edge. A k-sigma move scales as `sqrt(T)`, so R scales
as `sqrt(T)` and **cost/R falls as `1 / sqrt(T)`**.

> **H-Scalp-3.** The displacement → retest → continuation mechanism is a
> property of order flow, not of the 15-minute clock. At a longer horizon its
> gross edge is unchanged while cost/R falls as `1/sqrt(T)`, and the strategy
> becomes net positive.

This is the same arithmetic confirmed live earlier today, where widening the
stop moved net monotonically (`rho(net, mult) = +1.000` in all four cells of
`run_stop_width`). There it bought nothing because gross was zero. Here there
is a gross to protect.

## Pre-registered prediction (quantitative, written before running)

Taking H-Scalp-2's measured `gross = +0.1156` and `cost/R = 0.1359` at 15m:

| bars | expected cost/R | expected net if gross holds |
|---|---|---|
| 15m (reference) | 0.1359 | −0.0203 |
| 30m | 0.0961 | +0.0195 |
| 60m | 0.0680 | +0.0476 |
| 120m | 0.0481 | +0.0675 |
| 240m | 0.0340 | +0.0816 |

**Two things are being tested at once and they are reported separately:**

1. **Does cost/R fall as `1/sqrt(T)`?** Nearly mechanical; a large miss means
   the move-size scaling assumption is wrong and the rest is uninterpretable.
2. **Does gross survive?** The real question. A longer horizon pushes the
   target further into the future, where whatever the displacement knew may
   have decayed. **If `gross` declines with `T` faster than `cost/R` does, the
   hypothesis is false and no horizon fixes it.**

## Design — everything inherited from H-Scalp-2 except the clock

| parameter | value |
|---|---|
| symbols | BTCUSD, ETHUSD, SOLUSD, XRPUSD (unchanged) |
| signal | `z = r_t / stdev(r, 96 bars ending t−1)`, strictly causal |
| threshold | `k = 3.0` primary; 2.5 / 3.5 robustness. No other values |
| entry | passive limit at `close_t − retest × (close_t − open_t)` |
| retest | 0.33 primary; 0.25 / 0.50 robustness. No other values |
| invalidation | price trades back through `open_t` before fill → cancel |
| ambiguous bar | one bar spanning entry and invalidation → skip (conservative) |
| entry window | 8 bars |
| R | `entry − open_t` — a property of the event, not tuned |
| target / stop | +1.0R / −1.0R; same-bar resolves to the STOP |
| time exit | 8 bars from entry |
| execution | maker/maker primary, conservative fill; all four exec models reported |
| costs | Delta India per-symbol maker/taker × 1.18 GST + 2 bps slippage; stop and time exits always taker; funding at snapshot crossings |

**The one new parameter:** `bar_minutes ∈ {15, 30, 60, 120, 240}`. 15m is the
reference cell and must reproduce the registry; it is not a candidate.

**`VOL_LOOKBACK` stays at 96 BARS, not 24 hours.** Holding the bar count fixed
keeps the z-score estimator's sampling properties identical across timeframes,
so a change in results cannot be an artifact of estimating sigma from 24 bars
at one horizon and 96 at another. The calendar window lengthens as a
consequence, and that is accepted.

**No new simulator.** `bar_minutes` was threaded through the existing
`hscalp2.run` with a default of 15, verified to reproduce the recorded
experiment: `cost/R 0.1359` identical to four decimals, `n = 2,467` against
2,466 recorded — one extra event, because the candle store gained ~2 hours of
bars after the original run was recorded at 2026-08-12T08:38.

## Data splits

Chronological, identical to H-Scalp-1 and H-Scalp-2:

- **train** 2025-01-01 → 2025-07-01
- **valid** 2025-07-01 → 2026-01-01
- **test** 2026-01-01 → 2026-08-12

## THERE IS NO CLEAN TEST SET, AND THIS IS THE MAIN WEAKNESS

H-Scalp-2 already spent the 2026 test window, and its result there is known
(`gross +0.1295`, `net −0.0236`). H-Scalp-3 is the same mechanism on the same
symbols over the same data; a longer bar length does not make that window
naive. **Any "test" number here is contaminated and will not be treated as
out-of-sample evidence.**

Consequences, accepted in advance:

- Selection across the `bar_minutes` grid is permitted on **validation only**.
- The test window is computed and reported for completeness and is **not**
  eligible to support any classification above PROMISING BUT UNPROVEN.
- **A positive result cannot be confirmed by backtest.** Confirmation requires
  a forward paper arm, pre-registered before it starts, under a stopping rule
  frozen across the whole family — the error recorded in
  `docs/v3_stopping_rule.md`, where a per-arm rule was written after one arm
  was already running and abandoned the same day.

## Multiple comparisons

This is the **twelfth** test in a program with eleven nulls, and the grid is
5 timeframes × 3 k × 3 retest × 4 exec models × 2 fill models = **360 cells**.
The primary cell is fixed in advance: `bar_minutes` chosen on validation,
`k = 3.0`, `retest = 0.33`, `maker/maker`, `conservative`. Every other cell is
a robustness check, not a candidate. A result that appears only in a
non-primary cell is noise and is recorded as such.

## Classification rule (decided in advance)

Evaluated on **train and validation only**, at the primary cell:

| verdict | condition |
|---|---|
| **PROMISING BUT UNPROVEN** | net > 0 in train **and** validation, gross positive on all 4 symbols, and net survives a 1.5× cost stress |
| **EXECUTION-DEPENDENT** | net > 0 only under maker/maker with optimistic fills |
| **NO ECONOMIC EDGE** | gross > 0 at some horizon but net ≤ 0 at every horizon |
| **NO SIGNAL** | gross ≤ 0, or gross decays with `T` faster than cost/R |

**Nothing here can be classified ROBUST or VALIDATED EDGE**, because no clean
test set exists. The best available outcome is PROMISING BUT UNPROVEN followed
by a forward test.

## What would falsify the hypothesis

`gross` declining monotonically with `bar_minutes`. If the continuation
mechanism is a microstructure effect that decays within an hour, the longer
horizons will show it directly, and the answer is that the edge is real,
unreachable, and this program is finished.

<!-- FROZEN ABOVE THIS LINE -->

SHA-256 of everything above the marker, computed at freeze time (before the run):

    a0f176beb8c5e40d4faad07404070b50c8743c104494277262a16e6c1f09fbcb

---

## RESULT — recorded 2026-08-21, after the run

**Verdict: NO SIGNAL.** The pre-registered rule fired on its own terms.

### Prediction 1 — CONFIRMED

`cost/R` fell as `1/sqrt(T)`, slightly faster at the long end:

| bars | observed cost/R | predicted | ratio |
|---|---|---|---|
| 15 | 0.1089 | 0.1089 | 1.000 |
| 30 | 0.0753 | 0.0770 | 0.978 |
| 60 | 0.0529 | 0.0544 | 0.972 |
| 120 | 0.0330 | 0.0385 | 0.857 |
| 240 | 0.0222 | 0.0272 | 0.815 |

The arithmetic worked exactly as stated. Friction is not what killed this.

### Prediction 2 — FAILED, and failed informatively

`gross` by horizon, the two windows disagreeing as strongly as it is possible
to disagree:

| window | 15m | 30m | 60m | 120m | 240m | rho |
|---|---|---|---|---|---|---|
| train 2025H1 | +0.1429 | +0.1194 | +0.0772 | +0.0070 | −0.0776 | **−1.000** |
| valid 2025H2 | +0.0655 | +0.1106 | +0.1474 | +0.2150 | +0.1059 | **+0.400** |

**Train shows perfect monotone decay with horizon. Validation shows the
opposite.** A real order-flow mechanism does not reverse its horizon dependence
between two adjacent half-years. This is the finding, and it is stronger
evidence than the verdict itself: whatever produced H-Scalp-2's positive gross
is not stable in the dimension the hypothesis proposed to exploit.

### The selected cell

Selection was on validation net, as frozen. It chose **120m**:

- validation net **+0.1741**, `t_boot 2.339`, CI **[+0.025, +0.314]** — excludes zero
- survives 1.5× cost stress at **+0.1557**
- **train net −0.0259**
- gross positive on **3 of 4** symbols (ETHUSD −0.0328)
- `n = 97`, `n_eff = 62`

The rule required net > 0 in **both** train and validation. It is not met. Had
the rule been written after seeing this, the validation column alone would have
looked like a discovery — a significant t, a clean CI, surviving cost stress,
on one cell out of 360. That is precisely what pre-registration is for, and it
is the second time today the same trap appeared.

### Post-hoc observation, recorded as post-hoc

The **30m** cell is net positive in all three windows (+0.0441 / +0.0197 /
+0.0328) with 4 of 4 symbols gross-positive. Every confidence interval includes
zero and the combined Stouffer `z` is only ≈1.36.

**This was not the pre-registered selection and is not a result.** It is
recorded so it is not rediscovered and mistaken for one later. Promoting it
would require a fresh, uncontaminated test window, which does not exist for
this family — the 2026 data was spent by H-Scalp-2 and has now been looked at
again here.

### Consequence

Twelve tests, twelve nulls. The one mechanism with a positive gross does not
survive the horizon extension that the cost arithmetic required, and its
horizon dependence is unstable across windows. There is no remaining
pre-registered hypothesis in this program.

---

## CORRECTION — the frozen date is wrong

**This was frozen on 2026-08-23, not 2026-08-21.** The date was taken from the
last trade in the live sample rather than from the clock; confirmed against the
RDS snapshot `deltabt-paper-final-v3-v4-20260823t185433z`, created
2026-08-23T18:54:39Z.

The frozen section is deliberately not edited, so its SHA-256
`a0f176beb8c5e40d4faad07404070b50c8743c104494277262a16e6c1f09fbcb` keeps
verifying. What the hash guarantees is unaffected: the hypothesis, the grid,
the selection rule and the classification thresholds were all fixed before the
run, and the run confirmed it by rejecting a cell whose validation column
looked like a discovery.

`data_end 2026-08-12` is unchanged and correct — the candle store genuinely
ends there, which is why the sweep saw no data after that date.
