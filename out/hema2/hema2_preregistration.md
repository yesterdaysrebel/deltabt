# H-EMA-2 — Pre-registration

**Status: FROZEN.** Written before any H-EMA-2 result was computed. Every
definition, threshold, seed, mapping and gate below is fixed. Nothing here may
be changed on the basis of a TRAIN or VALID outcome.

---

## 1. Question

Do EMA-derived mechanisms contain directional information that survives Delta's
transaction costs **and** a control matched on stop geometry, when moved from
the 1m grid to 5m / 15m / 1h?

This is not a search for a profitable EMA configuration. The pre-registered
outcome set includes "no mechanism beats its width-matched control", and that
result would be a success, not a failure of the experiment.

## 2. Why this experiment exists

H-EMA-1 rejected standalone 1m EMA crossover economically: five pre-declared
pairs, 6,355-36,155 TRAIN trades each, every gross expectancy within 1.2 sigma
of zero, net expectancy -0.67R to -1.15R. Its closing finding was a confound:

> cost/R tracks median stop width almost mechanically -- 27 bps -> 0.75 cost/R,
> 73 bps -> 0.35. The best pair's advantage is bought by selecting wide-stop
> trades.

H-Structure-1 independently reached the same conclusion from different data.
Two untested axes remain: **higher timeframes** (never run) and **mechanisms
other than the bare crossover** (never run). Both are tested here.

Because a higher timeframe widens the structural stop by construction, ANY
arm at 1h will show a lower cost/R than the same arm at 5m whether or not it
predicts direction. The width-matched control (S 7) exists solely to make that
distinguishable. Without it, a positive net expectancy at 1h is uninterpretable.

## 3. Frozen infrastructure

| Component | Source | Treatment |
|---|---|---|
| Simulator | `hwpr._simulate` | Unmodified. One position at a time, entry at next 1m open, MARK-price stop trigger, same-bar stop+target -> STOP, `i = m + 1` chaining. |
| Stop injection | `research/stops.py::injection_arrays` | Unmodified. Validates finiteness and long<short ordering at every signal bar. |
| Cost model | `costs.SymbolCosts` | Unmodified. Per-symbol taker x1.18 GST, 2.0 bps slippage, per-symbol funding cadence at snapshot crossings. |
| Resampling | `strategy.resample_ohlcv` | Unmodified. UTC-aligned. |
| ATR | `indicators.atr` | Wilder RMA of true range. |
| Supertrend | `indicators.supertrend(h,l,c,factor,atr_period)` | Pine argument order. `direction < 0` is bullish. |
| Inference | `research.stats` | Stationary bootstrap, cluster design effect. |
| Universe | `out/hwpr_universe.csv` | BTCUSD ETHUSD SOLUSD XRPUSD. |
| TRAIN | pinned | 2025-01-01 -> 2025-12-20 |
| VALID | pinned | 2025-12-20 -> 2026-04-16 |
| TEST | pinned | LOCKED. Not computed. |

`DATA_END = 1786531980` (2026-08-12 10:53Z). Splits are 60% / 80% of
`DATA_END - STUDY`, pinned as integers so no refetch can move them.

Deleted and NOT restored: `hema.py`, `run_hema.py`, `robust_hema.py`,
`audit_hema.py`, `out/ema_experiment/`. H-EMA-2 is implemented fresh.

## 4. EMA definition

No EMA exists in `indicators.py`. Implemented fresh in `research/hema2.py`.

    seed    SMA of the first `length` finite values, placed at that index
    recur   ema[t] = alpha * x[t] + (1 - alpha) * ema[t-1],  alpha = 2/(length+1)
    before  NaN for every index before the seed index

Strictly one-sided: `ema[t]` depends only on `x[:t+1]`. The SMA seed is the
TradingView convention and is chosen so the series is deterministic rather than
dependent on how much history precedes it. Verified in `tests/test_hema2.py`
against an independent recursive reference and against `pandas.ewm(adjust=False)`
seeded identically.

## 5. Execution architecture

    1m OHLCV (frozen cache)
       |
       +-- resample -> 5m / 15m / 1h  -> all features, all signals
       |
       +-- 1m grid -> fills, stops, targets, intrabar resolution

A signal computed on the execution-TF bar spanning `[T, T+tf)` is knowable only
at the instant `T+tf`. The order is placed at the open of the 1m bar opening at
`T+tf`. The signal is written to 1m index `e-1` so the frozen simulator's
"enter at the next bar's open" lands exactly on `e`.

Resolving fills on 1m is a refinement of intrabar resolution, never a
relaxation: a 1m walk can only find the stop earlier than a coarse walk, and it
reads only bars at or after the entry bar.

No incomplete higher-timeframe candle is ever read. Regime timeframes (S 11)
use the last FULLY CLOSED regime bar, mapped by
`slot = searchsorted(t_regime, t_exec - tf_regime, side='right') - 1`.

## 6. Stop rule (A1, final)

Frozen structural stop, evaluated on the EXECUTION timeframe:

    LONG   stop = min( supertrend(10, 2.0)[t], lowest low since the last flip )
    SHORT  stop = max( supertrend(10, 2.0)[t], highest high since the last flip )

computed on the execution-TF grid and projected causally onto 1m, then handed
to the frozen simulator through `injection_arrays`. This is H-WPR-1's stop
definition unchanged; only the grid it is evaluated on differs. Stop width
therefore scales with timeframe BY CONSTRUCTION, which is the effect the
matched control is designed to neutralise.

`max_stop_pct = 0.05` is KEPT (A3, final). At 1h some trades will be rejected
because the structural stop exceeds 5% of entry. This is a frozen selection
constraint, not a parameter. Every arm reports `eligible_signals`,
`skipped_stop`, `executed_trades`, and `skipped_stop / eligible_signals`, plus
the stop-width distribution before and after the cap.

A signal bar whose stops are non-finite, or where `stop_long >= stop_short`,
is dropped BEFORE `injection_arrays` is called (which would otherwise raise),
and counted as `skipped_invalid`. This is a degenerate case requiring
`supertrend == leg_low == leg_high` and is expected to be rare; it is reported
rather than silently absorbed.

## 7. Mechanisms

All five are crossover-derived so they remain directly comparable to each other
and to H-EMA-1. Crossover is an EVENT, not a state:

    LONG event   ema_fast[t] >  ema_slow[t]  AND  ema_fast[t-1] <= ema_slow[t-1]
    SHORT event  ema_fast[t] <  ema_slow[t]  AND  ema_fast[t-1] >= ema_slow[t-1]

### M1 - crossover
The bare event. 5 pairs x 3 TF = 15 arms.

### M2 - crossover + slow-EMA slope
    displacement       = ema_slow[t] - ema_slow[t-5]
    normalized_slope   = displacement / atr(14)[t]
This is a 5-BAR ATR-normalised displacement, not a per-bar rate.
    LONG  fires when the LONG event holds AND normalized_slope >= +threshold
    SHORT fires when the SHORT event holds AND normalized_slope <= -threshold
thresholds 0.00 / 0.25 / 0.50. At 0.00 the gate is "slope agrees in sign",
so M1 -> M2(0.00) -> M2(0.25) -> M2(0.50) is a monotone ladder and the
incremental information contributed by slope is read off the ladder.
5 pairs x 3 TF x 3 thresholds = 45 arms.

### M3 - crossover + volatility expansion
    rising   atr(14)[t] > atr(14)[t-1]
    ratio    atr(14)[t] / mean(atr(14)[t-20 : t]) > threshold
The mean window is the 20 bars STRICTLY BEFORE t, i.e. indices t-20..t-1
inclusive of neither endpoint ambiguity: `atr[t-20:t]` in Python slice terms.
Bar t is excluded from its own baseline. thresholds 1.2 / 1.5.
The volatility condition only gates the EMA direction; it never generates a
signal of its own. 5 pairs x 3 TF x 2 thresholds = 30 arms.

### M4 - crossover then pullback / re-entry
On a crossover event, ARM a setup carrying that event's direction. The setup
expires after 10 execution-TF bars. While armed, a re-entry fires on the first
bar where BOTH hold:
    proximity   |close[t] - ema_fast[t]| <= d * atr(14)[t]
    resumption  close[t] > close[t-1]  (long)   /  close[t] < close[t-1]  (short)
Firing disarms the setup. A new crossover replaces any armed setup. d = 0.25 /
0.50. No future bar is consulted to decide whether to arm.
5 pairs x 3 TF x 2 distances = 30 arms.

### M5 - higher-timeframe regime + crossover
    exec 5m  -> regime 1h
    exec 15m -> regime 4h
    exec 1h  -> regime 1D
Regime EMA pair is FIXED at 20/50 on the regime timeframe, for every arm.
    bull regime  ema20 > ema50 on the last fully closed regime bar
    bear regime  ema20 < ema50
A LONG event fires only in a bull regime; SHORT only in a bear regime.
5 exec pairs x 3 TF = 15 arms.

**Total: 135 arms.**

## 8. Controls

### C-b (PRIMARY) - stop-width-matched, direction-randomised
Answers: what would the same opportunity set and stop geometry return if the
signal carried no directional information?

Procedure, frozen:
1. Eligible population = every execution-TF bar in the window, past warmup,
   with a finite ordered stop pair, whose prospective stop width is within the
   5% cap. Prospective stop width uses the same projected 1m entry open the arm
   would have used, so it is knowable at signal time.
2. Deciles are defined on the ARM's realised `stop_pct` distribution for that
   (arm, symbol, split): 10 bins at the 0,10,...,100th percentiles. Bin edges
   come from the arm, never from the control.
3. For each decile, draw WITHOUT REPLACEMENT from eligible bars whose
   prospective stop width falls in that decile, as many as the arm realised in
   that decile.
4. If a decile has fewer eligible candidates than required, take all of them
   and record the shortfall in `decile_shortfall`. No substitution from
   neighbouring deciles.
5. Direction is an independent fair coin per drawn bar.
6. Identical stop, target, sizing, fee, slippage, funding and 1m fill path.

Sampling without replacement is required because a signal is a boolean array
position: a repeated index cannot be represented and would silently collapse.

Because the simulator chains positions (`i = m + 1`), the control's EXECUTED
trade count will not equal the number of bars drawn. Both are reported.

### C-a (SECONDARY, diagnostic) - timestamp-matched, direction-randomised
The arm's own entry bars, direction by fair coin, everything else identical.
Isolates direction alone. The C-a vs C-b difference is itself a measurement of
stop-geometry effects, since C-a inherits the arm's exact stop distribution
while C-b inherits only its width distribution.

### Seeds
Exactly five per control: **11, 23, 37, 53, 71**. Fixed here, stored in
`candidates.json`, chosen before any run. Reported per control: mean, median,
sd, min, max across seeds, and seed sensitivity.

## 9. Primary quantity

    excess_net = EMA net expectancy - C-b net expectancy

An arm with positive net expectancy that does not exceed C-b is NOT an EMA
edge; it is stop geometry. Secondary: `EMA net - C-a net`.

## 10. Gates

PROMISING requires ALL of:
  1. net expectancy > 0
  2. excess_net vs C-b materially > 0, outside the across-seed control spread
  3. adequate trade count (>= 200 executed trades on the split)
  4. stable across all five control seeds
  5. no leakage flagged
  6. TRAIN -> VALID behaviour consistent in sign and order of magnitude

INCONCLUSIVE if the sample is too small, the interval spans zero, or the
EMA/control difference is indistinguishable from noise.

DEAD if the arm fails economically, or fails to beat C-b, or its advantage
vanishes under width matching.

No significance threshold is invented to accommodate 135 arms, and the largest
t-statistic is explicitly NOT the selection criterion (S 12).

## 11. Multiplicity

135 arms is an explicit multiple-hypothesis exploration. The complete candidate
universe is reported, ranked, best and worst shown. Under 135 correlated tests
the maximum |t| from noise alone is approximately 2.8-3.0, so no arm is called
promising on a t-statistic. The final report discusses selection risk directly
and distinguishes four effects that must not be conflated: signal edge,
timeframe effect, stop-geometry effect, and selection effect.

## 12. Anti-leakage rules

No EMA period is optimised after TRAIN. No timeframe is chosen after VALID. No
stop definition is changed to improve a result. No future candle is read. No
VALID result selects a TRAIN candidate. Cost assumptions are not altered. No
arm is compared against a control with different stop geometry. No raw net
expectancy is reported without its matched-control comparison. Any suspected
leakage invalidates the affected result and is reported.

## 13. Supplementary out-of-sample

BEATUSD (listed 2026-01-05, so it has NO TRAIN data and cannot have influenced
any freeze) is run on VALID only, reported separately, and may not rescue a
failed arm. AKEUSD and BANKUSD are excluded: their entire history lies inside
the locked TEST window, so including them would be opening TEST.

## 14. Deviations from the specification

**THIS EXPERIMENT'S SEAL WAS BROKEN. H-EMA-2 IS SUPERSEDED BY H-EMA-3.**

Recorded honestly and in full after independent expert review of the TRAIN
results. Nothing below was known when TRAIN ran; everything below was found by
reviewers or by verification afterwards.

### D1 -- the recorded hash does not certify this file (CRITICAL)
`candidates.json` records
`3fceea5e2a552be6a2505c8078ec7bc90036c2641ecb2dc29f2d3849b5996c2b`, the hash of
this document as it stood at 03:45. Section 15 was appended at 03:55, so the
document TRAIN actually ran against hashed
`2e2c6868b60f98f2f3a1f1cf08ae12ca251c4d78127ae38afa68521b655393b9`.
The TRAIN report printed the 03:45 value as proof of the freeze. It proved
nothing. A pre-registration whose hash does not match its file is not frozen.

### D2 -- the primary metric was substituted after seeing the result (CRITICAL)
S 9 of this document names ONE primary quantity: `excess_net = EMA net - C-b
net`. On seeing that it was contaminated, the TRAIN report replaced it with
"excess gross" and justified the swap by calling that "a pre-registered S 17
output". **This document has 15 sections. There is no S 17, and the string
"excess gross" does not appear in the frozen text.** The cited section belongs
to the instructing message, not to this protocol. This is the exact substitution
S 12 forbids, and S 14 was left reading "None".

### D3 -- the contamination was misdiagnosed (CRITICAL)
The report attributed the control's inflated cost/R to the asymmetry between
`stop_long = min(ST, leg_lo)` and `stop_short = max(ST, leg_hi)`. That is wrong.
C-b's MEDIAN cost/R matches the arm's (0.2345 vs 0.2471 at 5m) and its median
stop is 3% WIDER; the gap exists only in the mean. The true cause is D4. The
asymmetry is a 10-14% effect on median width and cannot produce a 70% cost gap.
The diagnosis was correct for C-a and was over-generalised to C-b.

### D4 -- implementation defect: the bottom decile is open-ended (CRITICAL)
`control_cb` sets `edges[0] = -np.inf`, so the lowest stop-width bin admits
every eligible bar tighter than the arm's realised minimum, down to 0.0003% of
entry. Because cost/R ~ 2(taker+slip)/stop_pct, ~1-3% of such draws dominate the
control's mean cost. Closing the bin moves `excess_net` on `M1|5m|5/20` from
+0.168 to -0.013 -- a sign flip. The frozen primary metric should have been
repaired, not abandoned.

### D5 -- the control's direction is not a fair coin (MAJOR)
S 8 requires "an independent fair coin per drawn bar". The implementation draws
(bar, direction) PAIRS from a width decile, so direction is selected
conditional on stop width -- the coupling the control exists to break. P(long)
runs 0.468 to 0.568 across deciles. This was identified before TRAIN and I
stated it would be recorded here; it was not.

### D6 -- trades resolve using post-split data (MINOR)
`simulate` masks signals by window but the exit walk runs the full loaded array.
3 of 1,640 arm trades and 10 of 4,986 C-b trades exit after the TRAIN boundary,
the latest inside VALID. This is why `unresolved_at_boundary` is always 0.

### D7 -- required control diagnostics were discarded (MAJOR)
`control_cb` returns `meta` with requested/drawn/shortfall/collisions; the
runner unpacks only `b[0..3]` and drops it, so S 8.4's mandated
`decile_shortfall` was never persisted. Recovered after the fact:
shortfall 0, collisions 0.34%, executed/drawn 0.52-0.67 -- the control is
thinned a second time by the position lock after being matched to the arm's
already-thinned distribution.

### D8 -- the multiplicity bound contradicts the observed maximum (MAJOR)
S 11 states the noise ceiling for max |t| is "approximately 2.8-3.0". The
observed max is 3.995. Under the stated model that has probability ~0.9%, and
correlation lowers the ceiling rather than raising it, so the stated model does
not explain the observation.

### D9 -- the design cannot support its own conclusion (MAJOR)
Median effective n is 797 (deff 1.31) and per-trade sd is ~1.43R, giving a
per-arm minimum detectable effect of 0.14-0.16R. The report ruled out effects of
0.006-0.022R with an instrument 7-24x too blunt. A paired mirror-direction
estimator on identical data achieves se 0.0048R and detects a real +0.0193R
directional edge at the 1R barrier (t = 6.04) that this design could not see.

### D10 -- an undeclared parallel track was added after TRAIN
`hema2_dataset.py` and `out/hema2/dataset/` remove the one-position-at-a-time
rule to recover the 58.5% of setups it discarded. The frozen simulator was NOT
modified and the frozen results are unchanged, but the track postdates TRAIN and
is declared here rather than in the sealed text.

### Consequence
The economic conclusion of H-EMA-2 survives review and is independently
corroborated: the directional information in these mechanisms is 3-11x smaller
than the round-trip cost. But this protocol's integrity guarantees do not hold,
so the result is recorded as SUPERSEDED and the question is re-registered as
H-EMA-3 around an estimator that is scale-free, chaining-free and ~10x more
precise.

---

## 15. Reporting / observability layer (added before TRAIN, changes nothing frozen)

This section was appended BEFORE any TRAIN result existed. It adds reporting
only. No candidate, mechanism, simulator semantic, stop rule, cost, control or
gate is altered by it, and the journal may not be used to select candidates.

### 15.1 Setup lifecycle

Every arm reports a funnel, derived from arrays that already exist rather than
by instrumenting the frozen simulator:

    setups_detected      raw mechanism signals on the execution-TF grid
      - rejected_warmup            inside the arm's warmup
      - rejected_stop_invalid      non-finite or mis-ordered structural stop
      - rejected_no_entry_bar      no 1m bar exists at/after the TF close
      - rejected_outside_split     projected entry falls outside the window
    = eligible_setups      signals actually handed to the simulator
      - skipped_stop               r_price <= 0 or stop > 5% of entry
      - skipped_size               position rounds to zero contracts
      - rejected_position_open     a position was already open (i = m + 1)
    = trades_entered
      of which exit_reason == "end" are unresolved at the data boundary

`rejected_position_open` is computed as the residual
`eligible - skipped_stop - skipped_size - trades`, which is exact because the
frozen simulator has no other path that consumes a signal without trading.

### 15.2 Reconciliation invariant

For every arm, per symbol and in aggregate, within 1e-9 relative tolerance:

    sum(trade.r_gross)   == reported gross R
    sum(trade.fee_r)     == reported fees
    sum(trade.slip_r)    == reported slippage
    sum(trade.funding_r) == reported funding
    sum(trade.r_net)     == reported net R
    trades_entered       <= eligible_setups
    skipped_stop trades never appear in the trade journal

A reconciliation failure invalidates the arm rather than being rounded away.

### 15.3 Descriptive rankings (NOT selection)

After TRAIN, three pre-declared rankings identify arms for human inspection:
top 5 by `EMA net - C-b net`, top 5 by net expectancy, top 5 by worst-symbol
net expectancy. These are labelled DESCRIPTIVE in the report. They do not
alter the frozen manifest, do not gate VALID, and every one of the 135 arms is
reported regardless of rank.

Representative trades within a detailed arm are chosen deterministically: the 5
best and 5 worst by net R, and the 3 trades nearest the median net R. No trade
is picked by hand.

### 15.4 Output tree

    out/hema2/summaries/{arm,symbol,mechanism,timeframe}_summary.csv
    out/hema2/summaries/setup_funnel.csv
    out/hema2/trades/<candidate_id>_<symbol>_trades.csv     complete, machine-readable
    out/hema2/trades/<candidate_id>_<symbol>_journal.md     human-readable extract

Full trade CSVs are written only for the descriptively-ranked arms; every arm's
aggregates appear in the summary tables. Candidate ids are slugified for the
filesystem (`/` -> `-`, `|` -> `__`).
