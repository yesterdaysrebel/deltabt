# H-REL-1 — FINAL REPORT

Hypothesis 3 of 3 — the last in the MARKET PHENOMENON DISCOVERY phase.

> Relative movements among BTC, ETH, SOL and XRP contain short-horizon
> predictive information.

Selected formulation, frozen before TRAIN: **leader shock, follower
under-response**. BTC makes an unusually large 15m move; a follower does not
move as far in the same direction; does the follower close the gap?

Pre-registration `out/hrel1/hrel1_preregistration.md`, sha256
`0711bb59aa7e07779080e6c618a16b1ece0aa206ecc8e23c65ca8ec8d10fd586`.

---

## Journal

```
R1-LAG   (LAG_UP + LAG_DOWN)

    BTC shocks:             2,916   (5.2% of 15m bars)
    Events:                 1,527
    Long:                     835
    Short:                    692
    Day clusters:             249

    TRAIN  (effect = mean signed forward return)
    ------------------------------------------
    +5m         +0.0186%   t  +1.23
    +15m        +0.0382%   t  +1.60
    +30m        +0.0002%   t  +0.01
    +60m        +0.0191%   t  +0.48   <-- PRIMARY
    +240m       +0.0771%   t  +1.08
    +1440m      +0.0274%   t  +0.20

    MDE:                  +0.1123%
    Effect/MDE:              0.17x
    Control:              +0.0055%   p = 0.5570
    Symbols positive:           2/3
    Agreeing (A5):              2/3   (required 2)
    Per symbol:        ETHUSD +0.0247%  SOLUSD -0.0125%  XRPUSD +0.0378%
    Halves:            H1 +0.0044%   H2 +0.0350%

    VALID              NOT COMPUTED — Stage-A gate not passed on TRAIN

    Stage A:           INSUFFICIENT POWER

    FINAL:             INSUFFICIENT POWER
```

---

## Mandatory TRAIN / VALID table

| Metric | TRAIN | VALID |
|---|---:|---:|
| Events | 1,527 | not computed |
| Effect | +1.91 bps | not computed |
| MDE | +11.23 bps | not computed |
| Effect / MDE | 0.17x | not computed |
| Control | +0.55 bps | not computed |
| Symbols positive | 2/3 | not computed |
| Symbols agreeing with pooled sign (A5) | 2/3 | not computed |
| Gross R | not tested | not tested |
| Cost/R | not tested | not tested |
| Net R | not tested | not tested |

    INFORMATION:    INSUFFICIENT POWER
    REPLICATION:    NO  (VALID not run — TRAIN gate not passed)
    ECONOMIC:       NOT TESTED
    FINAL VERDICT:  INSUFFICIENT POWER

---

## Stage-A gate

| Gate | R1-LAG |
|---|---|
| A1 effect nonzero | PASS |
| A2 effect >= MDE | **FAIL** |
| A3 exceeds control | **FAIL** |
| A4 same sign H1/H2 | PASS |
| A5 >=2/3 followers agree | PASS |

A4 and A5 pass. A2 and A3 do not, and they are the ones that matter: the
effect cannot be distinguished from zero, nor from its own control
(p = 0.557). A consistent sign across halves and symbols is
not evidence when the quantity being signed is indistinguishable from noise.

---

## Power

| | H-STRUCTURE-2 | H-VOL-1 | H-REL-1 |
|---|---:|---:|---:|
| TRAIN events at +1h | 3,811 | 317 | 1,527 |
| day clusters | 353 | 160 | 249 |
| MDE at +1h | 4.98 bps | 8.30 bps | 11.23 bps |
| round-trip cost floor | 15.8 bps | 15.8 bps | 15.8 bps |
| cost floor / MDE | 3.2x | 1.9x | 1.4x |

1,527 events but only 249 day clusters, because three followers reacting to
the same BTC shock at the same timestamp are close to one observation rather
than three. The day cluster is what prevents that from being counted as three
times more evidence than it is; the iid standard error here would have been
3.09 bps against the cluster's 4.01 bps, a
1.3x understatement of uncertainty.

This is the tightest power margin of the three, but the MDE still sits below
the cost floor: an effect large enough to trade would have been detected.

---

## FINAL VERDICT

    H-REL-1:  INSUFFICIENT POWER

Stage B was not constructed. No percentile, leader, gap threshold or
timeframe was adjusted.

**This was the third and last hypothesis in the phase.** All three families
have now failed Stage A. Per the protocol's final stop rule the research
program stops here and produces a strategic diagnosis rather than a fourth
family. See `out/phase_discovery/strategic_diagnosis.md`.
