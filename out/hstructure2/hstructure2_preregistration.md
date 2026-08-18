# H-STRUCTURE-2 — PRE-REGISTRATION

Hypothesis 1 of the 3 available in the MARKET PHENOMENON DISCOVERY phase.
Protocol: `out/phase_discovery/research_protocol.md`, sha256
`74fcb799b80a094611500e94eadf167f616b3019fb0d16b625dabef241638a0f`.

Written and frozen BEFORE any event was counted and before any forward return
was computed. Nothing below may change once TRAIN runs.

---

## 1. HYPOTHESIS

> HH/HL and LH/LL structural transitions contain directional information about
> subsequent price movement.

The question at Stage A is **not** whether a profitable strategy can be built
from them. It is whether the events predict forward PRICE returns at all.

### 1.1 Relationship to H-Structure-1

H-Structure-1 tested whether structure transitions produced positive GROSS
expectancy under a 2R target and a structural stop, and returned NO SIGNAL.
That is a **joint** test of information and of one particular trade geometry,
and H-COST-1 and H-NULL-1 then showed that trade geometry can both destroy a
real effect and manufacture a fake one. H-STRUCTURE-2 removes the geometry
entirely: no stop, no target, no cost, no R. It measures forward price return.

A NO SIGNAL verdict on H-Structure-1 therefore does not pre-determine this
result in either direction, and this is not a re-run: the continuation event
below is strictly narrower than H-Structure-1's family C (it additionally
requires the standing high to be an HH), and the failure/reversal events were
never tested at all.

---

## 2. FROZEN EVENT DEFINITIONS

### 2.1 Swing detection — REUSED UNCHANGED

`_swing_flags` and `_structure_state` are imported from the H-Structure-1
archive `out/hstructure/code/hstructure.py`. They are not rewritten, not
re-tuned and not copied with edits.

    swing high at bar k  <=>  high[k] > high[j] for all j in [k-N, k+N], j != k
    swing low  at bar k  <=>  low[k]  < low[j]  for all j in [k-N, k+N], j != k

Strict inequality both sides; a tie is not a swing. A swing at bar k is
CONFIRMED only at the close of bar k+N and is invisible to every bar before
k+N. Confirmation delay is exactly N structure bars by construction. This code
passed H-Structure-1's anti-lookahead audit: structure state reproduced exactly
at 48 truncation points, and 0 violations across 36,732 trades.

On confirmation, a swing high is HH if above the previous confirmed swing high
else LH; a swing low is HL if above the previous confirmed swing low else LL.

### 2.2 Frozen parameters — ONE ARM, NO SWEEP

    structure timeframe  =  15m
    swing strength N     =  3
    trigger              =  ONESHOT (FALSE -> TRUE transition only)

**These are chosen a priori and are not swept.** Protocol section 7 forbids
turning the hypothesis into a parameter search, so the reasoning is recorded
here rather than discovered later:

- **N = 3** is the smallest strength that requires a genuine local extremum
  over a 7-bar window. N = 2 fires on micro-noise; N = 5 and N = 8 push the
  confirmation delay to 75 and 120 minutes on a 15m grid, past several of the
  pre-declared horizons, so the event would be stale relative to what is being
  measured.
- **15m** puts the confirmation delay at 45 minutes and typical swing spacing
  in the 1.5-5 hour range, which straddles the middle of the pre-declared
  horizon set. A 1h structure would align only with the two longest horizons
  and yield far fewer events; a 5m structure resolves below the shortest.
- **ONESHOT** because the hypothesis is about an EVENT. A level trigger would
  emit the same standing condition on consecutive bars and inflate the count
  with mechanically duplicated observations.

An event census is run at manifest freeze. It counts events only and reads no
forward returns. **It may not change N, the timeframe or the trigger.** If the
census shows too few events, the resulting verdict is INSUFFICIENT POWER — that
is the honest answer, not a licence to re-pick a parameter.

### 2.3 The four events

Let, at structure bar `t`, as of its close:
`H = last_h_px[t]` (last confirmed swing high), `L = last_l_px[t]` (last
confirmed swing low), and the classification flags `is_hh, is_lh, is_hl, is_ll`.

    CONT_LONG    HH -> HL -> break previous HH
                 is_hh & is_hl & (close > H)          direction +1

    CONT_SHORT   LL -> LH -> break previous LL
                 is_ll & is_lh & (close < L)          direction -1

    FAIL_SHORT   HH -> HL -> break HL
                 is_hh & is_hl & (close < L)          direction -1

    FAIL_LONG    LL -> LH -> break LH
                 is_ll & is_lh & (close > H)          direction +1

The set is closed and symmetric: from bull structure, a break upward is
continuation and a break downward is failure; from bear structure, the mirror.
No fifth event may be added after seeing results.

### 2.4 Two hypotheses, not one

CONTINUATION and FAILURE make **opposite** predictions from the **same** state,
so pooling all four would test nothing coherent. They are evaluated separately:

    S2-CONT   = CONT_LONG  + CONT_SHORT
    S2-FAIL   = FAIL_LONG  + FAIL_SHORT

Each faces the full Stage-A gate independently. Because exactly two
pre-declared families are tested, the Bonferroni-adjusted threshold alpha=0.025
is reported alongside alpha=0.05. No other partition of the events is reported
as a headline.

### 2.5 Conflicts

A bar satisfying a continuation and a failure condition simultaneously is
possible when the last confirmed low sits above the last confirmed high. Such
bars are **DROPPED**, not resolved, following the H-COST-1 precedent, and the
dropped count is reported.

---

## 3. STAGE A — MEASUREMENT

No stop, no target, no R, no fees, no slippage, no funding. Stage A may not
consult any of them.

### 3.1 Event timing (anti-lookahead)

A 15m structure bar spanning `[T, T+15m)` closes at instant `T+15m`. That is
the first instant its close, and any swing it confirms, is knowable. The
measurement reference price `P0` is the **OPEN of the first 1m bar at or after
`T+15m`**. Never the structure bar's own close, never a backdated price.

The entry 1m bar must pass `tradable_mask` (synthetic-bar and halt exclusion);
otherwise the event is dropped.

### 3.2 Forward returns

Pre-declared horizons, all six reported, none selected after the fact:

    +5m   +15m   +30m   +1h   +4h   +1d

For horizon `h` minutes: `P_h` = close of the 1m bar whose timestamp is
`t0 + (h-1)*60`, located by timestamp search, never by index arithmetic — the
1m cache has gaps, and index arithmetic would silently shorten a horizon across
one. If no bar exists within 60 s of the target timestamp the event is dropped
**at that horizon only**, and the drop count is reported per horizon.

    raw return     r_h  = P_h / P0 - 1
    signed return  y_h  = direction * r_h

`y_h` is the unit of analysis. Effect = `mean(y_h)`.

### 3.3 Split boundaries and the TEST lock

    STUDY = 2025-01-01,  DATA_END = 2026-08-12 10:53Z (pinned),  span = DATA_END - STUDY
    TRAIN = STUDY .. STUDY + 0.60*span   ->  2025-12-20
    VALID =       .. STUDY + 0.80*span   ->  2026-04-16
    TEST  = 2026-04-16 .. DATA_END       ->  LOCKED, NEVER COMPUTED

An event is admitted to split S at horizon h **iff**

    t0 >= S.start   AND   t0 + h <= S.end

The right-hand condition is not a nicety. Without it a +1d event near the end
of TRAIN reads VALID prices, and a +1d event near the end of VALID reads TEST
prices — which would break the TEST lock outright. Event counts therefore
differ by horizon, and each horizon reports its own n.

### 3.4 Reported per horizon

For S2-CONT and S2-FAIL, each broken out **long / short / pooled** and by
**BTCUSD / ETHUSD / SOLUSD / XRPUSD**:

    event count | mean return | median return | win rate
    effect | 95% CI | cluster t-stat | MDE | effect/MDE

---

## 4. INFERENCE — INHERITED, NOT INVENTED

The hierarchy ratified from H-NULL-1
(`out/hnull1/inference_promotion.json`) is used unchanged:

    PRIMARY               cluster
    SECONDARY DIAGNOSTIC  moving-block bootstrap
    DIAGNOSTIC            iid

`hnull1.inference()` is called as-is and is NOT modified. It predates the
ratification and still returns `se = se_block` when a block length is supplied,
so this experiment reads **`se_cluster`** explicitly for every headline number.
Modifying the frozen module would invalidate the hash recorded in
`inference_frozen.json`; reading the correct field costs nothing and leaves the
audit trail intact.

### 4.1 Cluster unit — declared, with reasons

    PRIMARY cluster unit = calendar UTC day, pooling ALL FOUR SYMBOLS

H-NULL-1 froze the cluster definition as non-overlapping 50-bet episodes, on a
single synthetic path with no symbol dimension. Two dependence structures here
are absent from that setting and would be missed by it:

1. **Within-symbol overlap.** At the +1d horizon, events hours apart share
   almost their entire return window. Their signed returns are near-copies.
2. **Cross-sectional correlation.** BTC, ETH, SOL and XRP move together. Four
   events at the same timestamp on four symbols are close to one observation,
   not four.

Clustering on the calendar day pooled across symbols absorbs both. Gate 6 of
H-NULL-1 requires that dependence be declared and inference matched to it; this
is that requirement being met, not a new estimator. TRAIN gives ~354 clusters
and VALID ~117, both comfortably above the ~30 needed for the t approximation.

Symbol-day clustering and the 50-event episode definition are reported as
diagnostics so the choice is visible and checkable.

### 4.2 MDE — mandatory beside every null claim

    MDE = 2.8 * SE_cluster

the identical construction used in H-NULL-1 (`2.8 ~= z_0.975 + z_0.80`, i.e.
95% confidence and 80% power), applied to the Stage-A statistic and therefore
denominated in fractional price return, not in R. The R-denominated production
MDE of 0.03462 R governs Stage B only.

**An effect below the MDE is INSUFFICIENT POWER. It is never NO EDGE.**

---

## 5. STAGE A CONTROL — FROZEN BEFORE TRAIN

    Timestamp-matched direction permutation.

Within each symbol, the direction labels (+1/-1) are randomly permuted across
that symbol's event set. Symbol, timestamp, timeframe, the event set itself and
the exact multiset of directions are all preserved; **only** the assignment of
direction to event is randomized. 1,000 permutations, seed frozen at 20260818.

A naive coin-flip control would be worse, and the reason matters: if the event
set is direction-imbalanced and the market drifted over the window, a fair coin
does not reproduce that imbalance, and the drift leaks into the signal as if it
were structure. Permuting the observed labels reproduces the imbalance exactly,
so the drift appears in the control too and cancels.

Reported: the permutation mean, its 95% central interval, and the exact
two-sided permutation p-value.

---

## 6. STAGE A GATE — PRIMARY HORIZON DECLARED IN ADVANCE

    PRIMARY HORIZON = +1h

Declared now because "any of six horizons passes" is a six-fold multiple test.
+1h is the centre of the log-spaced pre-declared set and is roughly four
structure bars, the scale at which a 15m structural break has had time to
resolve. If the gate is claimed on any non-primary horizon it must additionally
survive Bonferroni x6, and the report must say so explicitly.

    A1  TRAIN effect     effect at +1h is nonzero in the hypothesized direction
    A2  Power            |effect| >= MDE           else INSUFFICIENT POWER
    A3  Control          effect - control_mean >= MDE, and the effect lies
                         outside the 95% central interval of the permutation
                         distribution
    A4  Temporal         same sign at +1h in TRAIN-H1 and TRAIN-H2
                         (TRAIN split in half by time)
    A5  Cross-sectional  at least 3 of 4 symbols share the pooled sign at +1h
    A6  VALID            the frozen phenomenon replicates on VALID: same sign,
                         and effect >= MDE_valid

A1-A5 are decided on TRAIN. VALID is run ONCE, only if A1-A5 pass, and is never
consulted before that decision.

---

## 7. STAGE B — ONLY IF STAGE A PASSES

Not constructed here beyond the binding constraints, because protocol section 6
requires the Stage-A return curve to determine the economically plausible
horizon, and that curve does not exist yet.

Binding now:

- Stage-B candidates are pre-declared and frozen BEFORE the economic test.
- Entry, stop, exit, sizing, fees, slippage, funding and overlap rules are all
  fixed in that freeze.
- The frozen simulator (`hwpr._simulate`) and the production cost model are
  used unchanged.
- Gate 2 holds: no estimator may compare legs with different R.
- **No exit shopping.** If the frozen construction fails, the verdict is
  INFORMATION BUT NOT ECONOMIC and the hypothesis stops there.

---

## 8. VERDICT VOCABULARY

Exactly one of:

    NO INFORMATION
    INSUFFICIENT POWER
    INFORMATION - NOT REPLICATED
    INFORMATION BUT NOT ECONOMIC
    ECONOMIC EDGE

"Promising", "interesting" and "needs more tuning" are not verdicts.

---

## 9. WHAT WOULD FALSIFY THIS HYPOTHESIS

Stated in advance so the failure is not renegotiated afterwards:

- Effect at +1h below the MDE on TRAIN -> INSUFFICIENT POWER, stop.
- Effect indistinguishable from the permutation control -> NO INFORMATION, stop.
- Sign flips between TRAIN-H1 and TRAIN-H2 -> NO INFORMATION, stop.
- Effect carried by one symbol alone -> fails A5, stop.
- Passes TRAIN, fails VALID -> INFORMATION - NOT REPLICATED, stop.

In every one of those cases the response is the verdict and the stop. Not a
filter, not a threshold change, not a different timeframe, not an added
confirmation indicator. Any of those would be a new hypothesis, and the phase
budget is three.

---

## 10. UNIVERSE

    BTCUSD, ETHUSD, SOLUSD, XRPUSD

BEATUSD is excluded: listed 2026-01-05, no TRAIN data.
AKEUSD and BANKUSD are excluded: listed 2026-07-22, entire history inside the
locked TEST window, so including them would BE opening TEST.
