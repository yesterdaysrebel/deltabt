# H-VOL-1 — PRE-REGISTRATION

Hypothesis 2 of the 3 available in the MARKET PHENOMENON DISCOVERY phase.
Protocol `out/phase_discovery/research_protocol.md`, sha256
`74fcb799b80a094611500e94eadf167f616b3019fb0d16b625dabef241638a0f`.

Frozen before any event was counted and before any forward return was computed.

---

## 1. HYPOTHESIS

> Volatility compression followed by expansion contains directional
> information.

Stage A asks only whether the expansion direction predicts forward PRICE
returns. Not whether a strategy can be built from it.

### 1.1 Relationship to H-Compress-1 / H-Compress-1-rev2

Both returned NO SIGNAL. Neither settles this question, for the same reason
H-Structure-1 did not settle H-STRUCTURE-2:

- They measured **gross R under a specific execution construction** — a passive
  limit order at the broken boundary with a 3-bar lifetime, plus a volume
  confirmation and a body-size filter. That bundles the information question
  with an entry mechanism and a trade geometry.
- They had **169 and 227 trades**. The production MDE at that sample is far too
  wide to have ruled out anything.

H-VOL-1 removes the execution construction entirely, keeps only the compression
*state*, and measures price.

### 1.2 Why the compression definition is INHERITED, not chosen now

The compression state is reused **exactly** from H-Compress-1, which
pre-registered and froze it before that experiment ran:

    15m grid
    atr_pct[t]     = ATR(14)[t] / close[t]
    threshold[t]   = 20th percentile of atr_pct over the trailing 960 bars,
                     the window ENDING AT t-1 and excluding t
    compressed[t]  = atr_pct[t] < threshold[t]
    zone           = >= 4 consecutive compressed bars, with
                     (zone_high - zone_low) / ATR[t] <= 1.5

Implementation is `hcompress._rolling_quantile_causal` and
`hcompress._compression_zones`, imported unchanged.

This is deliberate. Choosing fresh thresholds today — a percentile, a window, a
minimum duration — would be three new numbers picked by me after two related
experiments had already failed, and no reader could tell whether they were
picked to work. Numbers frozen in a prior pre-registration cannot have been.

What is DROPPED from H-Compress-1 is everything that belongs to execution and
not to the state: the retest entry, the 3-bar order lifetime, the volume
multiple and the body-size filter. Stage A has no execution, so it may not
inherit execution parameters.

---

## 2. FROZEN EVENT DEFINITION

One event type. One arm. No sweep.

    V1-EXP   at 15m bar t, expansion out of a valid compression zone:

             ok[t-1]                        a valid zone existed as of t-1
             AND close[t] > zone_high[t-1]  -> direction +1
             OR  close[t] < zone_low[t-1]   -> direction -1

    ONESHOT trigger: FALSE -> TRUE transitions only.

The two branches are mutually exclusive because `zone_low <= zone_high`, so
unlike H-STRUCTURE-2 there is no conflict case to drop. This is asserted in the
code rather than assumed.

Only ONE family is tested. The hypothesis as stated is that expansion carries
direction; a fade/failure mirror is not part of it and is not added.

### 2.1 Anti-lookahead

`zone_high[t-1]`, `zone_low[t-1]` and `ok[t-1]` are computed from bars up to and
including t-1. The percentile window ends at t-1 and excludes t. The breakout
test uses `close[t]`, knowable at the close of bar t. The event is therefore
knowable at `time[t] + 15m`, and not before.

---

## 3. EVERYTHING ELSE IS INHERITED UNCHANGED

This is the point of a frozen methodology: H-VOL-1 changes the event definition
and nothing else. All of the following are taken from the phase protocol and
from H-STRUCTURE-2's frozen machinery, byte for byte, by import:

    reference price      OPEN of the first 1m bar at or after the 15m close
    tradability          tradable_mask on that 1m bar
    horizons             +5m +15m +30m +1h +4h +1d
    PRIMARY horizon      +1h                       (declared before TRAIN)
    horizon location     by TIMESTAMP, never index arithmetic
    split admission      t0 >= start AND t0 + h <= end   (keeps TEST locked)
    inference PRIMARY    cluster, unit = calendar UTC day pooled across symbols
    MDE                  2.8 * SE_cluster
    control              within-symbol direction permutation,
                         1000 perms, seed 20260818
    gate                 A1-A6 exactly as in the protocol
    universe             BTCUSD ETHUSD SOLUSD XRPUSD
    splits               TRAIN -> 2025-12-20, VALID -> 2026-04-16, TEST LOCKED

An event census runs at manifest freeze. It counts events only, reads no
forward return, and **may not change the compression definition**. If the counts
are small the verdict is INSUFFICIENT POWER.

---

## 4. STAGE B — ONLY IF STAGE A PASSES

Not constructed. Candidates are pre-declared and frozen before the economic
test; the frozen simulator and production cost model are used unchanged; Gate 2
holds. No exit shopping — if the frozen construction fails the verdict is
INFORMATION BUT NOT ECONOMIC and the hypothesis stops.

---

## 5. WHAT WOULD FALSIFY THIS HYPOTHESIS

- Effect at +1h below the MDE on TRAIN -> INSUFFICIENT POWER, stop.
- Indistinguishable from the permutation control -> NO INFORMATION, stop.
- Sign flips between TRAIN-H1 and TRAIN-H2 -> NO INFORMATION, stop.
- Carried by one symbol alone -> fails A5, stop.
- Passes TRAIN, fails VALID -> INFORMATION - NOT REPLICATED, stop.

In every case the response is the verdict and the stop. Not a different
percentile, not a longer window, not a volume filter, not a different
timeframe. Those would be a new hypothesis, and after this one exactly one
remains: H-REL-1.

---

## 6. VERDICT VOCABULARY

    NO INFORMATION | INSUFFICIENT POWER | INFORMATION - NOT REPLICATED
    INFORMATION BUT NOT ECONOMIC | ECONOMIC EDGE
