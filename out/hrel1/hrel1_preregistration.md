# H-REL-1 — PRE-REGISTRATION

Hypothesis 3 of 3 — the last in the MARKET PHENOMENON DISCOVERY phase.
Protocol `out/phase_discovery/research_protocol.md`, sha256
`74fcb799b80a094611500e94eadf167f616b3019fb0d16b625dabef241638a0f`.

Frozen before any event was counted and before any forward return was computed.

---

## 1. HYPOTHESIS

> Relative movements among BTC, ETH, SOL and XRP contain short-horizon
> predictive information.

The protocol offers four illustrative formulations — leader/laggard response,
cross-asset shock, relative-strength divergence, relative-strength continuation
— and requires that **ONE concrete formulation** be selected before TRAIN.

### 1.1 The selected formulation: leader shock, follower under-response

    BTC makes an unusually large 15m move.
    A follower does not move as far in the same direction.
    Does the follower subsequently close the gap?

This is the lead-lag formulation. It is chosen over the other three for reasons
recorded here, in advance:

- It is **directional by construction**. The sign of the prediction is the sign
  of the leader's move; nothing about direction is fitted.
- It needs **one new threshold** (what counts as a shock) and no others. The
  under-response condition is a sign test against zero, not a tuned gap size.
- The alternatives are worse on exactly that axis. "Relative-strength
  divergence" and "continuation" both require a strength measure, a lookback
  and a divergence threshold — three new numbers, which section 7 of the
  protocol treats as the boundary between a hypothesis and a parameter search.

### 1.2 BTC is the leader, declared a priori

BTC is designated the leader before any measurement: it is the largest asset by
market capitalisation and the reference asset of the venue. The leader is not
selected by trying each of the four and keeping whichever leads best — that
would be a four-arm search reported as one hypothesis.

The event universe is therefore the three followers **ETHUSD, SOLUSD, XRPUSD**.
BTC cannot lag itself.

---

## 2. FROZEN EVENT DEFINITION

One event family. One arm.

On the 15m grid, with `r_s[t]` the log return of symbol `s` over bar `t`:

    shock[t]        |r_BTC[t]| >= 95th percentile of |r_BTC| over the trailing
                    960 bars, the window ENDING AT t-1 and excluding t

    under[f,t]      sign(r_BTC[t]) * (r_BTC[t] - r_f[t]) > 0
                    i.e. follower f moved LESS far than BTC in BTC's direction

    R1-LAG          shock[t] AND under[f,t],  direction = sign(r_BTC[t])

    ONESHOT trigger, per follower.

Both 15m bars must exist for BTC and for the follower at `t`; the two series
are inner-joined on timestamp, never forward-filled.

### 2.1 The one new number, and the one reused machine

The **95th percentile** is the only threshold introduced by this experiment. It
is the conventional definition of a tail, and it is the mirror of the 20th
percentile that H-Compress-1 froze for the low tail and H-VOL-1 inherited. It
is not swept.

The percentile itself is computed by `hcompress._rolling_quantile_causal`,
imported unchanged — the same causal trailing-window estimator, with the same
960-bar window, already used by two prior experiments. Its defining property is
that the window **ends at t-1 and excludes t**, so a bar can never help decide
whether it is itself unusual.

### 2.2 Anti-lookahead

`shock[t]` uses `r_BTC[t]`, knowable at the close of bar `t`. `under[f,t]` uses
`r_f[t]`, knowable at the same instant. The threshold uses only bars before `t`.
The event is therefore knowable at `time[t] + 15m` and not before, and the
reference price is the OPEN of the follower's first 1m bar at or after that
instant.

---

## 3. EVERYTHING ELSE IS INHERITED UNCHANGED

Imported from `hstructure2`, which is hash-frozen in its own manifest and is not
edited:

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
    splits               TRAIN -> 2025-12-20, VALID -> 2026-04-16, TEST LOCKED

The day cluster matters more here than in either previous hypothesis and is
worth naming: three followers reacting to the SAME BTC shock at the SAME
timestamp are close to one observation, not three. Pooling all symbols within a
calendar day absorbs that.

### 3.1 The single gate modification, declared in advance

Gate **A5** requires "at least 3 of 4 symbols share the pooled sign". The event
universe here has only **three** symbols, because the leader is excluded, so
3-of-4 is unreachable and would fail automatically — a bug, not a gate.

A5 is therefore declared as **at least 2 of the 3 followers**, the nearest
analogue. This is stated before TRAIN and is the only deviation from the
protocol's gate; A1, A2, A3, A4 and A6 are unchanged.

---

## 4. STAGE B — ONLY IF STAGE A PASSES

Not constructed. Candidates pre-declared and frozen before the economic test;
frozen simulator and production cost model unchanged; Gate 2 holds. No exit
shopping — if the frozen construction fails the verdict is INFORMATION BUT NOT
ECONOMIC and the hypothesis stops.

---

## 5. WHAT WOULD FALSIFY THIS HYPOTHESIS

- Effect at +1h below the MDE on TRAIN -> INSUFFICIENT POWER, stop.
- Indistinguishable from the permutation control -> NO INFORMATION, stop.
- Sign flips between TRAIN-H1 and TRAIN-H2 -> NO INFORMATION, stop.
- Fewer than 2 of 3 followers agree -> fails A5, stop.
- Passes TRAIN, fails VALID -> INFORMATION - NOT REPLICATED, stop.

Not a different percentile. Not a different leader. Not a gap threshold. Not a
different timeframe. This is the last hypothesis in the phase: if it fails, the
protocol requires the research program to STOP and produce a strategic
diagnosis, not a fourth family.

---

## 6. VERDICT VOCABULARY

    NO INFORMATION | INSUFFICIENT POWER | INFORMATION - NOT REPLICATED
    INFORMATION BUT NOT ECONOMIC | ECONOMIC EDGE
