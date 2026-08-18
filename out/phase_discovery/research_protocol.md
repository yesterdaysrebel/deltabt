# MARKET PHENOMENON DISCOVERY — FROZEN RESEARCH PROTOCOL

Frozen 2026-08-18, before any hypothesis in this phase was defined.

This document is the operator's protocol, transcribed. It is frozen so that when
a hypothesis fails I cannot quietly relax the rule that would have stopped me.
Every constraint below was written down BEFORE the first event was counted.

---

## 0. STATUS OF THE PRECEDING PHASE

Methodology development is CLOSED. H-NULL-1 established and validated the
production estimator. No H-NULL-2. No new estimator. No new statistical
framework. No further strengthening of controls after seeing a result.

The purpose of this phase is MARKET PHENOMENON DISCOVERY, not methodology
development.

---

## 1. HARD RESEARCH BUDGET

Exactly three hypothesis families are available:

    1. H-STRUCTURE-2
    2. H-VOL-1
    3. H-REL-1

Maximum three. There is no H-STRUCTURE-3, H-VOL-2 or H-REL-2 in this phase.
The list is not expanded because all three fail.

If all three fail, the required report is:

> No sufficiently strong, reproducible predictive phenomenon was found under the
> current data, universe, timeframe and execution assumptions.

That is an acceptable and valuable result.

---

## 2. NO ITERATIVE STRATEGY OPTIMIZATION

Each hypothesis gets exactly one pass:

    DEFINE -> PRE-REGISTER -> FREEZE MANIFEST -> TRAIN -> DECIDE -> VALID ONCE -> FINAL VERDICT

The prohibited loop is TRAIN -> tweak -> TRAIN -> tweak -> choose best -> VALID.

Once TRAIN starts, the hypothesis definition is IMMUTABLE.

---

## 3. TWO-STAGE DESIGN

### STAGE A — INFORMATION

> Does the phenomenon predict future price movement?

Stage A must NOT use strategy R, stop placement, target placement or trading
costs to decide whether the phenomenon contains information. It measures forward
PRICE returns only.

Pre-declared horizons, all reported, none selected after the fact:

    +5m   +15m   +30m   +1h   +4h   +1d

For each horizon report:

    event count | mean return | median return | win rate
    effect | 95% CI | cluster t-stat | MDE | effect/MDE

Broken out by: long, short, pooled — and by BTCUSD, ETHUSD, SOLUSD, XRPUSD.

### STAGE B — ECONOMIC TEST

Only Stage-A survivors get an executable strategy. Pre-declare entry, stop,
exit, position sizing, fees, slippage, funding and overlap rules. Use the frozen
simulator and the production cost model. Do not optimize the strategy to rescue
the hypothesis.

---

## 4. STAGE A CONTROL

A pre-declared timestamp-matched control. It preserves symbol, timestamp and
timeframe, and randomizes ONLY the hypothesized directional/state component.

The control is frozen before TRAIN. No control is invented after seeing TRAIN.

---

## 5. STAGE A GATE

    A1  TRAIN effect      the phenomenon produces a meaningful directional effect
    A2  Power             observed effect >= production MDE
    A3  Control           the phenomenon materially exceeds the pre-declared control
    A4  Temporal          same sign in TRAIN-H1 and TRAIN-H2
    A5  Cross-sectional   not driven entirely by one symbol, unless the hypothesis
                          explicitly predicted a symbol-specific effect
    A6  VALID             the frozen phenomenon replicates on VALID

If effect < MDE the verdict is INSUFFICIENT POWER. It is never NO EDGE.

Only a hypothesis clearing A1-A6 proceeds to Stage B.

---

## 6. NO EXIT SHOPPING

Prohibited: 2R fails -> 1R -> trailing -> time exit -> 0.5R -> ...

The Stage-A return curve determines the economically plausible horizon. Stage-B
candidates are pre-declared before the economic test. If the frozen economic
construction fails, the verdict is INFORMATION BUT NOT ECONOMIC, and the search
stops there.

---

## 7. NO PARAMETER RESCUE

A hypothesis is not a parameter sweep. Prohibited: sweeping lookback 3..20 x
threshold 0.1..1.0 x timeframe x stop x target, then choosing the best cell.

A hypothesis must represent a SPECIFIC market phenomenon. If the phenomenon
cannot be defined without a large parameter search, stop and redesign it as a
future hypothesis rather than silently converting it into optimization.

---

## 8. FAILURE STOP RULE

If a hypothesis fails Stage A, that hypothesis STOPS. Specifically prohibited as
a response to failure: adding filters, changing thresholds, changing timeframe,
changing the definition, adding EMA / ADX / WPR confirmation, adding volatility
or regime filters, changing entry, changing exit.

Each of those would constitute a NEW hypothesis, and the budget is three.

## 8b. ECONOMIC FAILURE STOP RULE

If Stage A passes and Stage B fails: INFORMATION BUT NOT ECONOMIC. Stop. Do not
spend another experiment trying to monetize it. The objective is to discover
whether the market contains usable information, not to force every information
signal into profitability.

---

## 9. EARLY STOP RULE

If a hypothesis shows a strong TRAIN effect AND strong VALID replication AND
cross-symbol consistency AND effect >= MDE, then STOP the broad research queue.
Do not automatically run the remaining hypotheses. Investigate the successful
phenomenon deeply, using only robustness tests already pre-declared or required
by an obvious implementation concern.

The objective is ONE genuine phenomenon, not three completed experiments.

---

## 10. REQUIRED FINAL CLASSIFICATION

Every hypothesis ends in exactly one of:

    NO INFORMATION
    INSUFFICIENT POWER
    INFORMATION — NOT REPLICATED
    INFORMATION BUT NOT ECONOMIC
    ECONOMIC EDGE

"Promising", "interesting", "maybe" and "needs more tuning" are not verdicts and
may not appear unaccompanied by one of the five above.

Note: this vocabulary is the PHASE vocabulary and is deliberately different from
`registry.Experiment.VALID`, which is older and describes strategy outcomes. The
mapping is recorded per experiment; nothing is added to `Experiment.VALID`.

---

## 11. MANDATORY TRAIN / VALID TABLE

Every final report contains:

| Metric | TRAIN | VALID |
|---|---:|---:|
| Events | | |
| Effect | | |
| MDE | | |
| Effect / MDE | | |
| Control | | |
| Symbols positive | | |
| Gross R | | |
| Cost/R | | |
| Net R | | |

followed explicitly by:

    INFORMATION:    YES / NO / INSUFFICIENT POWER
    REPLICATION:    YES / NO
    ECONOMIC:       YES / NO / NOT TESTED
    FINAL VERDICT:  ...

Plus a short human-readable journal summary. The machine-readable outputs remain
authoritative.

---

## 12. METHODOLOGY FREEZE

Ratified from H-NULL-1 (see out/hnull1/inference_promotion.json):

    cluster inference  = PRIMARY
    iid                = diagnostic
    moving-block       = secondary diagnostic

    production MDE     = 0.03462 R at 2,400 bets, scaling as 1/sqrt(n)

These are not modified because a future result looks inconvenient. No new
estimator. No new null family.

If an ACTUAL implementation bug is discovered: stop, document it, fix it, rerun
the affected frozen experiment. If no actual defect exists: continue.

The seven gates carried forward from H-NULL-1 remain mandatory:

    G1  the canonical zero-signal null behaves as calibrated
    G2  EQUAL-R: no estimator may compare legs with different R
    G3  the wider-stop artifact is classified INVALID, never PROMISING
    G4  a planted edge is recovered
    G5  the MDE is reported beside every null claim
    G6  direction persistence is declared and inference matched to it
    G7  economics are tested only after G1-G6

---

## 13. ABSOLUTE ANTI-LOOP RULE

Before ANY additional experiment, four questions:

    Is this required to answer the currently frozen hypothesis?
        NO  -> do not run it.

    Am I changing the hypothesis because the previous result was inconvenient?
        YES -> do not run it.

    Am I adding complexity because I haven't found an edge?
        YES -> do not run it.

    Would I have pre-declared this before seeing TRAIN?
        NO  -> do not use it to select a winner.

---

## 14. FINAL STOP AFTER THREE FAMILIES

If H-STRUCTURE-2, H-VOL-1 and H-REL-1 all fail, the entire current research
program STOPS. Prohibited responses: proposing H-STRUCTURE-3, H-MOMENTUM-1,
H-MICROSTRUCTURE-1, H-ORDERFLOW-1, H-EMA-4, H-WPR-2, H-ADX-2.

The required deliverable instead is a strategic diagnosis covering data
resolution, available instruments, execution costs, trading horizon,
signal-to-noise ratio, and whether the venue and data are suitable for this
style of trading at all. The decision that follows is the operator's: change
market, timeframe, data, venue, horizon — or stop the trading research.

---

## 15. THE ACTUAL OBJECTIVE

Do NOT optimize for: number of experiments, number of indicators, TRAIN Sharpe,
best backtest, number of green cells.

Optimize for ONE reproducible market phenomenon satisfying:

    directional information + out-of-sample replication
        + sufficient power + plausible economic monetization

If none is found after three deliberately different families, ACCEPT THE RESULT.
Do not manufacture a fourth family because we dislike the answer.

---

## 16. STANDING CONSTRAINTS (unchanged, carried forward)

- The TEST segment (2026-04-16 onward) is LOCKED and must NEVER be computed.
- Frozen experiments are not modified: H-WPR-1, `hwpr._simulate`.
- Production and paper-trading configuration is not modified.
- The registry is APPEND-ONLY. Never overwrite, never delete.
- Gate 2 is not weakened: `safe_paired_excess` must structurally REJECT
  unequal-R comparisons rather than return a number.
- Nothing is subtracted from any result to make a null look like zero.
